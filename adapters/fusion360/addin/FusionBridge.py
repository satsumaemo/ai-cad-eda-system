"""Fusion 360 Bridge Add-in — HTTP server + CustomEvent architecture.

Fusion 360 내부에서 실행되며:
1. 데몬 스레드에서 HTTP 서버(18080)를 연다
2. 외부 POST /execute 요청을 수신하면 CustomEvent를 발동
3. 메인 UI 스레드의 이벤트 핸들러가 Fusion API를 호출
4. 결과를 threading.Event로 HTTP 스레드에 돌려준다

설치:
1. 이 폴더(FusionBridge/)를 Fusion 360 AddIns 디렉토리에 복사 또는 심볼릭 링크
   Windows: %APPDATA%\\Autodesk\\Autodesk Fusion 360\\API\\AddIns\\FusionBridge
2. Fusion 360 → UTILITIES → Scripts and Add-Ins → Add-Ins 탭 → FusionBridge 실행
"""

import json
import threading
import time
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

try:
    import adsk.core
    import adsk.fusion

    _IN_FUSION = True
except ImportError:
    _IN_FUSION = False

# ─── 설정 ───

HOST = "127.0.0.1"
PORT = 18080
CUSTOM_EVENT_ID = "FusionBridgeExecuteEvent"
SHOW_PALETTE_EVENT_ID = "FusionBridgeShowPaletteEvent"
REQUEST_TIMEOUT = 120.0  # 초

# ─── 글로벌 상태 ───

_app: "adsk.core.Application | None" = None
_custom_event: "adsk.core.CustomEvent | None" = None
_show_palette_event: "adsk.core.CustomEvent | None" = None
_http_server: HTTPServer | None = None
_server_thread: threading.Thread | None = None
_stop_flag = False

# 커맨드 핸들러 레지스트리
_command_handlers: dict[str, Any] = {}

# 요청-응답 동기화: request_id → {"event": Event, "result": dict}
_pending: dict[str, dict] = {}
_pending_lock = threading.Lock()

_NO_BODY_ERROR: dict = {
    "status": "failure",
    "error": "바디가 없습니다. 먼저 형상을 생성하세요.",
    "result": {},
}


def _require_body(root) -> dict | None:
    """bRepBodies가 비어 있으면 에러 dict를 반환, 있으면 None."""
    if root.bRepBodies.count == 0:
        return dict(_NO_BODY_ERROR)
    return None


# ─── CustomEvent 핸들러 (메인 UI 스레드) ───


class BridgeEventHandler(adsk.core.CustomEventHandler if _IN_FUSION else object):
    """CustomEvent 수신 → 메인 스레드에서 명령 실행 → 결과 전달."""

    def notify(self, args):
        try:
            event_data = json.loads(args.additionalInfo)
            request_id = event_data["request_id"]
            action = event_data["action"]
            parameters = event_data.get("parameters", {})
            context = event_data.get("context", {})

            result = _execute_action(action, parameters, context)
        except Exception:
            result = {
                "status": "failure",
                "error": f"EventHandler error: {traceback.format_exc()}",
                "result": {},
            }
            try:
                request_id = json.loads(args.additionalInfo).get("request_id", "unknown")
            except Exception:
                request_id = "unknown"

        # HTTP 스레드에 결과 전달
        with _pending_lock:
            entry = _pending.get(request_id)
            if entry is not None:
                entry["result"] = result
                entry["event"].set()


class ShowPaletteEventHandler(adsk.core.CustomEventHandler if _IN_FUSION else object):
    """ShowPalette CustomEvent → 메인 스레드에서 팔레트 표시."""

    def __init__(self):
        if _IN_FUSION:
            super().__init__()

    def notify(self, args):
        try:
            app = adsk.core.Application.get()
            ui = app.userInterface
            _create_palette(ui)
        except Exception:
            pass


# ─── 명령 실행 ───


def _execute_action(action: str, parameters: dict, context: dict) -> dict:
    """등록된 핸들러로 명령을 실행한다."""
    if action == "_internal_snapshot":
        return _handle_snapshot(parameters)

    handler = _command_handlers.get(action)
    if handler is None:
        return {"status": "failure", "error": f"Unknown action: {action}", "result": {}}

    return handler(parameters, context)


def _handle_snapshot(parameters: dict) -> dict:
    """설계 스냅샷을 저장한다."""
    if not _IN_FUSION or _app is None:
        return {"status": "failure", "error": "Not in Fusion environment", "result": {}}
    try:
        doc = _app.activeDocument
        if doc:
            doc.save("AI System Checkpoint")
            return {"status": "success", "result": {"saved": True}}
        return {"status": "warning", "result": {"saved": False, "reason": "No active document"}}
    except Exception as e:
        return {"status": "failure", "error": str(e), "result": {}}


_MM_TO_CM = 0.1


def _get_construction_plane(root, plane_name: str):
    """평면 이름으로 construction plane을 반환한다."""
    plane_map = {
        "xy": root.xYConstructionPlane,
        "xz": root.xZConstructionPlane,
        "yz": root.yZConstructionPlane,
    }
    return plane_map.get(plane_name, root.xYConstructionPlane)


def _handle_create_rectangle_sketch(parameters: dict, context: dict) -> dict:
    """평면에 사각형 스케치를 생성한다."""
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent

        plane = _get_construction_plane(root, parameters.get("plane", "xy"))
        sketch = root.sketches.add(plane)

        x = parameters.get("x_mm", 0) * _MM_TO_CM
        y = parameters.get("y_mm", 0) * _MM_TO_CM
        w = parameters["width_mm"] * _MM_TO_CM
        h = parameters["height_mm"] * _MM_TO_CM

        p1 = adsk.core.Point3D.create(x, y, 0)
        p2 = adsk.core.Point3D.create(x + w, y + h, 0)
        sketch.sketchCurves.sketchLines.addTwoPointRectangle(p1, p2)

        return {
            "status": "success",
            "result": {
                "sketch_name": sketch.name,
                "profile_count": sketch.profiles.count,
                "type": "rectangle",
                "width_mm": parameters["width_mm"],
                "height_mm": parameters["height_mm"],
            },
        }
    except Exception:
        return {"status": "failure", "error": traceback.format_exc(), "result": {}}


def _handle_create_circle_sketch(parameters: dict, context: dict) -> dict:
    """평면에 원형 스케치를 생성한다."""
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent

        plane = _get_construction_plane(root, parameters.get("plane", "xy"))
        sketch = root.sketches.add(plane)

        cx = parameters.get("center_x_mm", 0) * _MM_TO_CM
        cy = parameters.get("center_y_mm", 0) * _MM_TO_CM
        r = parameters["radius_mm"] * _MM_TO_CM

        center = adsk.core.Point3D.create(cx, cy, 0)
        sketch.sketchCurves.sketchCircles.addByCenterRadius(center, r)

        return {
            "status": "success",
            "result": {
                "sketch_name": sketch.name,
                "profile_count": sketch.profiles.count,
                "type": "circle",
                "radius_mm": parameters["radius_mm"],
            },
        }
    except Exception:
        return {"status": "failure", "error": traceback.format_exc(), "result": {}}


def _handle_get_design_info(parameters: dict, context: dict) -> dict:
    """현재 설계 정보를 조회한다."""
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        if design is None:
            return {"status": "failure", "error": "No active design", "result": {}}

        root = design.rootComponent
        result: dict[str, Any] = {"design_name": root.name}

        if parameters.get("include_parameters", True):
            params = []
            for i in range(design.userParameters.count):
                p = design.userParameters.item(i)
                params.append({"name": p.name, "value": p.value, "unit": p.unit, "expression": p.expression})
            result["parameters"] = params

        if parameters.get("include_bodies", True):
            bodies = []
            for i in range(root.bRepBodies.count):
                b = root.bRepBodies.item(i)
                bodies.append({"name": b.name, "id": b.entityToken, "is_visible": b.isVisible})
            result["bodies"] = bodies

        if parameters.get("include_bounding_box", True):
            bbox = root.boundingBox
            if bbox.isValid:
                result["bounding_box"] = {
                    "min": [bbox.minPoint.x * 10, bbox.minPoint.y * 10, bbox.minPoint.z * 10],
                    "max": [bbox.maxPoint.x * 10, bbox.maxPoint.y * 10, bbox.maxPoint.z * 10],
                }

        if parameters.get("include_mass_properties", False):
            for i in range(root.bRepBodies.count):
                b = root.bRepBodies.item(i)
                props = b.physicalProperties
                result.setdefault("mass_properties", []).append({
                    "body": b.name,
                    "volume_mm3": props.volume * 1000,
                    "area_mm2": props.area * 100,
                })

        return {"status": "success", "result": result}
    except Exception:
        return {"status": "failure", "error": traceback.format_exc(), "result": {}}


def _handle_get_body_properties(parameters: dict, context: dict) -> dict:
    """특정 바디의 물리적 속성을 조회한다."""
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent

        err = _require_body(root)
        if err:
            return err

        body_id = parameters["body_id"]
        body = None
        for i in range(root.bRepBodies.count):
            b = root.bRepBodies.item(i)
            if b.name == body_id or b.entityToken == body_id:
                body = b
                break
        # body_id 매칭 실패 시 마지막 바디 사용
        if body is None and root.bRepBodies.count > 0:
            body = root.bRepBodies.item(root.bRepBodies.count - 1)
        if body is None:
            return {"status": "failure", "error": f"No body found", "result": {}}

        props = body.physicalProperties
        bbox = body.boundingBox
        result = {
            "body_name": body.name,
            "volume_mm3": props.volume * 1000,
            "surface_area_mm2": props.area * 100,
            "center_of_mass": [props.centerOfMass.x * 10, props.centerOfMass.y * 10, props.centerOfMass.z * 10],
            "bounding_box": {
                "min": [bbox.minPoint.x * 10, bbox.minPoint.y * 10, bbox.minPoint.z * 10],
                "max": [bbox.maxPoint.x * 10, bbox.maxPoint.y * 10, bbox.maxPoint.z * 10],
            },
        }
        return {"status": "success", "result": result}
    except Exception:
        return {"status": "failure", "error": traceback.format_exc(), "result": {}}


def _handle_create_component(parameters: dict, context: dict) -> dict:
    """새 컴포넌트를 생성한다."""
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent

        name = parameters["name"]
        parent_id = parameters.get("parent_component_id")

        parent = root
        if parent_id:
            for occ in root.allOccurrences:
                if occ.component.id == parent_id:
                    parent = occ.component
                    break

        occ = parent.occurrences.addNewComponent(adsk.core.Matrix3D.create())
        occ.component.name = name

        return {
            "status": "success",
            "result": {
                "component_name": occ.component.name,
                "component_id": occ.component.id,
            },
        }
    except Exception:
        return {"status": "failure", "error": traceback.format_exc(), "result": {}}


def _handle_fillet(parameters: dict, context: dict) -> dict:
    """모서리에 필렛을 적용한다. 전체 실패 시 에지별 개별 시도."""
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent

        err = _require_body(root)
        if err:
            return err

        radius_cm = parameters["radius_mm"] * 0.1
        all_edges = parameters.get("all_edges", False)
        edge_ids_raw = parameters.get("edge_ids_csv", "") or parameters.get("edge_ids", [])
        if isinstance(edge_ids_raw, str):
            edge_ids = [e.strip() for e in edge_ids_raw.split(",") if e.strip()]
        else:
            edge_ids = edge_ids_raw

        fillets = root.features.filletFeatures
        body = root.bRepBodies.item(root.bRepBodies.count - 1)

        edge_list = []
        if all_edges:
            for j in range(body.edges.count):
                edge_list.append(body.edges.item(j))
        else:
            for body_idx in range(root.bRepBodies.count):
                b = root.bRepBodies.item(body_idx)
                for j in range(b.edges.count):
                    e = b.edges.item(j)
                    if str(e.tempId) in edge_ids:
                        edge_list.append(e)

        # 1차 시도: 모든 에지 한번에
        edges = adsk.core.ObjectCollection.create()
        for e in edge_list:
            edges.add(e)

        try:
            fillet_input = fillets.createInput()
            fillet_input.addConstantRadiusEdgeSet(
                edges, adsk.core.ValueInput.createByReal(radius_cm), True
            )
            fillet = fillets.add(fillet_input)
            return {"status": "success", "result": {"feature_name": fillet.name, "edge_count": edges.count}}
        except Exception:
            pass

        # 2차 시도: 모든 good_edges를 한번에 add
        good_edges = adsk.core.ObjectCollection.create()
        for e in edge_list:
            test = adsk.core.ObjectCollection.create()
            test.add(e)
            try:
                fi = fillets.createInput()
                fi.addConstantRadiusEdgeSet(test, adsk.core.ValueInput.createByReal(radius_cm), False)
                good_edges.add(e)
            except Exception:
                continue

        if good_edges.count == 0:
            return {"status": "failure", "error": "필렛 적용 가능한 모서리가 없습니다", "result": {}}

        try:
            fillet_input = fillets.createInput()
            fillet_input.addConstantRadiusEdgeSet(
                good_edges, adsk.core.ValueInput.createByReal(radius_cm), True
            )
            fillet = fillets.add(fillet_input)
            return {"status": "success", "result": {
                "feature_name": fillet.name,
                "edge_count": good_edges.count,
                "skipped_edges": len(edge_list) - good_edges.count,
            }}
        except Exception:
            pass

        # 3차 시도: 에지를 하나씩 개별 필렛으로 적용
        applied = 0
        skipped = 0
        last_fillet_name = ""
        for i in range(good_edges.count):
            single = adsk.core.ObjectCollection.create()
            single.add(good_edges.item(i))
            try:
                fi = fillets.createInput()
                fi.addConstantRadiusEdgeSet(
                    single, adsk.core.ValueInput.createByReal(radius_cm), True
                )
                f = fillets.add(fi)
                applied += 1
                last_fillet_name = f.name
            except Exception:
                skipped += 1
                continue

        if applied > 0:
            return {"status": "success", "result": {
                "feature_name": last_fillet_name,
                "edge_count": applied,
                "skipped_edges": len(edge_list) - applied,
            }}

        return {"status": "failure", "error": "필렛을 적용할 수 없습니다 (기하학적 제약)", "result": {}}
    except Exception:
        return {"status": "failure", "error": traceback.format_exc(), "result": {}}


def _handle_chamfer(parameters: dict, context: dict) -> dict:
    """모서리에 챔퍼를 적용한다. 전체 실패 시 에지별 개별 시도."""
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent

        err = _require_body(root)
        if err:
            return err

        distance_cm = parameters["distance_mm"] * 0.1
        all_edges = parameters.get("all_edges", False)
        edge_ids_raw = parameters.get("edge_ids_csv", "") or parameters.get("edge_ids", [])
        if isinstance(edge_ids_raw, str):
            edge_ids = [e.strip() for e in edge_ids_raw.split(",") if e.strip()]
        else:
            edge_ids = edge_ids_raw

        chamfers = root.features.chamferFeatures
        body = root.bRepBodies.item(root.bRepBodies.count - 1)

        edge_list = []
        if all_edges:
            for j in range(body.edges.count):
                edge_list.append(body.edges.item(j))
        else:
            for body_idx in range(root.bRepBodies.count):
                b = root.bRepBodies.item(body_idx)
                for j in range(b.edges.count):
                    e = b.edges.item(j)
                    if str(e.tempId) in edge_ids:
                        edge_list.append(e)

        # 1차 시도: 모든 에지 한번에
        edges = adsk.core.ObjectCollection.create()
        for e in edge_list:
            edges.add(e)

        try:
            chamfer_input = chamfers.createInput2()
            chamfer_input.edges = edges
            chamfer_input.setToEqualDistance(
                adsk.core.ValueInput.createByReal(distance_cm)
            )
            chamfer = chamfers.add(chamfer_input)
            return {"status": "success", "result": {"feature_name": chamfer.name, "edge_count": edges.count}}
        except Exception:
            pass

        # 2차 시도: 검증된 에지를 한번에 add
        good_edges = adsk.core.ObjectCollection.create()
        for e in edge_list:
            test_edges = adsk.core.ObjectCollection.create()
            test_edges.add(e)
            try:
                ci = chamfers.createInput2()
                ci.edges = test_edges
                ci.setToEqualDistance(adsk.core.ValueInput.createByReal(distance_cm))
                good_edges.add(e)
            except Exception:
                continue

        if good_edges.count == 0:
            return {"status": "failure", "error": "챔퍼 적용 가능한 모서리가 없습니다", "result": {}}

        try:
            chamfer_input = chamfers.createInput2()
            chamfer_input.edges = good_edges
            chamfer_input.setToEqualDistance(
                adsk.core.ValueInput.createByReal(distance_cm)
            )
            chamfer = chamfers.add(chamfer_input)
            return {"status": "success", "result": {
                "feature_name": chamfer.name,
                "edge_count": good_edges.count,
                "skipped_edges": len(edge_list) - good_edges.count,
            }}
        except Exception:
            pass

        # 3차 시도: 에지를 하나씩 개별 챔퍼로 적용
        applied = 0
        skipped = 0
        last_chamfer_name = ""
        for i in range(good_edges.count):
            single = adsk.core.ObjectCollection.create()
            single.add(good_edges.item(i))
            try:
                ci = chamfers.createInput2()
                ci.edges = single
                ci.setToEqualDistance(adsk.core.ValueInput.createByReal(distance_cm))
                c = chamfers.add(ci)
                applied += 1
                last_chamfer_name = c.name
            except Exception:
                skipped += 1
                continue

        if applied > 0:
            return {"status": "success", "result": {
                "feature_name": last_chamfer_name,
                "edge_count": applied,
                "skipped_edges": len(edge_list) - applied,
            }}

        return {"status": "failure", "error": "챔퍼를 적용할 수 없습니다 (기하학적 제약)", "result": {}}
    except Exception:
        return {"status": "failure", "error": traceback.format_exc(), "result": {}}


# 영어 → 한국어 재질 검색어 매핑 (Fusion 360 한국어판 대응)
_MATERIAL_SEARCH_ALIASES = {
    "aluminum": ["aluminum", "알루미늄"],
    "steel": ["steel", "강", "스틸"],
    "stainless": ["stainless", "스테인리스", "스텐"],
    "copper": ["copper", "구리"],
    "titanium": ["titanium", "티타늄", "티탄"],
    "brass": ["brass", "황동"],
    "nylon": ["nylon", "나일론"],
    "abs": ["abs"],
    "pla": ["pla"],
}


def _handle_set_material(parameters: dict, context: dict) -> dict:
    """바디에 재질을 할당한다 (한국어판 Fusion 대응)."""
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent

        err = _require_body(root)
        if err:
            return err

        body_id = parameters.get("body_id", "")
        material_name = parameters["material_name"]

        # 바디 찾기 (name 매칭, 실패 시 마지막 바디)
        body = None
        for i in range(root.bRepBodies.count):
            b = root.bRepBodies.item(i)
            if b.name == body_id:
                body = b
                break
        if body is None:
            body = root.bRepBodies.item(root.bRepBodies.count - 1)

        # 검색 키워드 결정 (영어 → 한국어 별칭 포함)
        search_terms = [material_name.lower()]
        for key, aliases in _MATERIAL_SEARCH_ALIASES.items():
            if key in material_name.lower():
                search_terms.extend(aliases)
                break

        # 재질 찾기
        material = None
        for lib in app.materialLibraries:
            for i in range(lib.materials.count):
                mat = lib.materials.item(i)
                if any(term in mat.name.lower() for term in search_terms):
                    material = mat
                    break
            if material:
                break

        if material is None:
            return {"status": "failure", "error": f"Material not found: {material_name}", "result": {}}

        body.material = material

        return {
            "status": "success",
            "result": {"body_name": body.name, "material_name": material.name},
        }
    except Exception:
        return {"status": "failure", "error": traceback.format_exc(), "result": {}}


def _sanitize_script(code: str) -> str:
    """LLM 생성 스크립트의 알려진 Fusion API 오류를 교정한다."""
    import re

    # FeatureDirections — 존재하지 않는 API 제거
    code = re.sub(r",\s*adsk\.fusion\.FeatureDirections\.\w+", "", code)
    code = re.sub(r"adsk\.fusion\.FeatureDirections\.\w+,?\s*", "", code)
    code = re.sub(r",\s*adsk\.fusion\.ExtentDirections\.\w+", "", code)

    # FeatureOperations 축약형 수정 (이미 올바른 풀네임은 건너뜀)
    _OP_FIX = {
        "Join": "JoinFeatureOperation",
        "Cut": "CutFeatureOperation",
        "NewBody": "NewBodyFeatureOperation",
        "Intersect": "IntersectFeatureOperation",
    }
    for short, full in _OP_FIX.items():
        # "FeatureOperations.Join" 은 매칭하되 "FeatureOperations.JoinFeatureOperation"은 건너뜀
        code = re.sub(
            rf"FeatureOperations\.{short}(?!FeatureOperation)\b",
            f"FeatureOperations.{full}",
            code,
        )

    # setDistanceExtent 3인자 → 2인자
    code = re.sub(
        r"setDistanceExtent\(\s*(?:True|False)\s*,\s*([^,)]+)\s*,\s*[^)]+\)",
        r"setDistanceExtent(False, \1)",
        code,
    )

    # profiles[index] → profiles.item(index)
    code = re.sub(r"\.profiles\[(\d+)\]", r".profiles.item(\1)", code)

    # shellFeatures.createInput 3인자 → 2인자
    code = re.sub(
        r"(shellFeatures\.createInput\(\s*[^,]+,\s*(?:True|False))\s*,\s*.+?\)\s*\)",
        r"\1)",
        code,
    )

    # SurfaceTypes enum: XXXSurface → XXXSurfaceType
    code = re.sub(
        r"SurfaceTypes\.(\w+?)Surface\b(?!Type)",
        r"SurfaceTypes.\1SurfaceType",
        code,
    )

    # 컬렉션 인덱싱 교정: .faces[i] → .faces.item(i) 등
    for attr in ("faces", "edges", "bodies", "bRepBodies", "sketches",
                  "occurrences", "features"):
        code = re.sub(
            rf"\.{attr}\[([^\]]+)\]",
            rf".{attr}.item(\1)",
            code,
        )

    return code


def _handle_execute_script(parameters: dict, context: dict) -> dict:
    """임의의 Fusion Python 스크립트를 실행한다.

    stdout(print)를 캡처하여 결과를 수집한다.
    스크립트의 run()은 print(json.dumps({...}))로 결과를 출력해야 한다.
    return 값도 폴백으로 수용하지만, print가 우선.
    """
    import io
    import sys

    script_code = parameters.get("script_code", "")
    if not script_code.strip():
        return {"status": "failure", "error": "Empty script_code", "result": {}}

    try:
        script_code = _sanitize_script(script_code)
    except Exception as e:
        return {"status": "failure", "error": f"Script sanitize error: {e}", "result": {}}

    # stdout 캡처 (exec + run 전체 기간)
    old_stdout = sys.stdout
    captured = io.StringIO()

    try:
        exec_globals: dict = {"__builtins__": __builtins__}
        sys.stdout = captured

        exec(script_code, exec_globals)

        # run() 호출 — stdout 캡처 유지한 상태로
        return_value = None
        if "run" in exec_globals:
            return_value = exec_globals["run"]({})

        # stdout 복원
        sys.stdout = old_stdout
        stdout_text = captured.getvalue().strip()

        # 1순위: stdout에서 JSON 추출 (print(json.dumps(...)) 결과)
        if stdout_text:
            # 여러 줄일 수 있으므로 마지막 JSON 라인 찾기
            for line in reversed(stdout_text.split("\n")):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        parsed = json.loads(line)
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        continue

        # 2순위: return 값 (폴백)
        if isinstance(return_value, dict):
            return return_value
        if isinstance(return_value, str):
            try:
                return json.loads(return_value)
            except json.JSONDecodeError:
                return {"status": "success", "result": {"output": return_value}}

        # 3순위: stdout 텍스트 그대로
        if stdout_text:
            return {"status": "success", "result": {"output": stdout_text}}

        return {"status": "success", "result": {"executed": True}}
    except SyntaxError as e:
        sys.stdout = old_stdout
        return {
            "status": "failure",
            "error": f"SyntaxError: {e.msg} (line {e.lineno})\n{e.text or ''}",
            "result": {"stdout": captured.getvalue().strip()},
        }
    except Exception:
        sys.stdout = old_stdout
        stdout_text = captured.getvalue().strip()
        error_msg = traceback.format_exc()
        return {
            "status": "failure",
            "error": error_msg,
            "result": {"stdout": stdout_text} if stdout_text else {},
        }


def _handle_get_selection(parameters: dict, context: dict) -> dict:
    """현재 Fusion에서 선택된 면/엣지/피처 정보를 반환한다."""
    if not _IN_FUSION or _app is None:
        return {"status": "success", "result": {"selection": [], "count": 0}}

    try:
        ui = _app.userInterface
        active_sel = ui.activeSelections
        selection: list[dict[str, Any]] = []

        for i in range(active_sel.count):
            entity = active_sel.item(i).entity
            info: dict[str, Any] = {
                "type": entity.objectType.split("::")[-1],
                "id": getattr(entity, "entityToken", str(i)),
                "index": i,
            }

            # 면(BRepFace) 정보
            if hasattr(entity, "area"):
                info["area_cm2"] = entity.area
                info["area_mm2"] = entity.area * 100

            # 면 법선 (면인 경우)
            if hasattr(entity, "evaluator") and hasattr(entity, "centroid"):
                try:
                    success, normal = entity.evaluator.getNormalAtPoint(entity.centroid)
                    if success:
                        info["normal"] = [round(normal.x, 4), round(normal.y, 4), round(normal.z, 4)]
                    info["centroid_mm"] = [
                        round(entity.centroid.x * 10, 2),
                        round(entity.centroid.y * 10, 2),
                        round(entity.centroid.z * 10, 2),
                    ]
                except Exception:
                    pass

            # 엣지 정보 (면인 경우 — 해당 면의 모든 엣지 ID 포함)
            if hasattr(entity, "edges"):
                info["edge_count"] = entity.edges.count
                edge_ids = []
                for j in range(entity.edges.count):
                    edge = entity.edges.item(j)
                    edge_ids.append(str(edge.tempId))
                info["edge_ids"] = edge_ids

            selection.append(info)

        return {"status": "success", "result": {"selection": selection, "count": len(selection)}}
    except Exception:
        return {"status": "success", "result": {"selection": [], "count": 0}}


# ─── HTTP 서버 (데몬 스레드) ───


def _fire_and_wait(action: str, parameters: dict, context: dict) -> dict:
    """CustomEvent를 발동하고 메인 스레드의 결과를 대기한다."""
    import uuid

    request_id = uuid.uuid4().hex[:12]
    event_obj = threading.Event()

    with _pending_lock:
        _pending[request_id] = {"event": event_obj, "result": None}

    payload = json.dumps({
        "request_id": request_id,
        "action": action,
        "parameters": parameters,
        "context": context,
    }, ensure_ascii=False)

    if _IN_FUSION and _app and _custom_event:
        _app.fireCustomEvent(CUSTOM_EVENT_ID, payload)
    else:
        # Fusion 외부 (테스트용): 직접 실행
        result = _execute_action(action, parameters, context)
        with _pending_lock:
            _pending[request_id]["result"] = result
            _pending[request_id]["event"].set()

    completed = event_obj.wait(timeout=REQUEST_TIMEOUT)

    with _pending_lock:
        entry = _pending.pop(request_id, None)

    if not completed or entry is None or entry["result"] is None:
        return {
            "status": "failure",
            "error": f"Timeout ({REQUEST_TIMEOUT}s): Fusion did not respond",
            "result": {},
        }

    return entry["result"]


class BridgeHTTPHandler(BaseHTTPRequestHandler):
    """HTTP 요청을 처리한다. 스레드풀에서 실행."""

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"

        path = self.path.rstrip("/")

        if path == "/execute":
            self._handle_execute(raw)
        elif path == "/chat":
            self._handle_chat(raw)
        elif path == "/heartbeat":
            self._send_json(200, {"acknowledged": True})
        elif path == "/snapshot":
            self._handle_snapshot(raw)
        else:
            self._send_json(404, {"error": f"Not found: {path}"})

    def do_GET(self):
        path = self.path.rstrip("/")

        if path == "/health":
            self._handle_health()
        elif path == "/show_palette":
            self._handle_show_palette()
        elif path == "/selection":
            self._handle_selection()
        elif path == "/pending":
            with _pending_lock:
                ids = list(_pending.keys())
            self._send_json(200, {"count": len(ids), "request_ids": ids})
        elif path == "/handlers":
            self._send_json(200, {"handlers": sorted(_command_handlers.keys())})
        else:
            self._send_json(404, {"error": f"Not found: {path}"})

    def _handle_execute(self, raw: bytes):
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "Invalid JSON"})
            return

        action = body.get("action", "")
        parameters = body.get("parameters", {})
        context = body.get("context", {})

        result = _fire_and_wait(action, parameters, context)
        self._send_json(200, result)

    def _handle_snapshot(self, raw: bytes):
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}
        result = _fire_and_wait(
            "_internal_snapshot",
            {"project_id": body.get("project_id", ""), "step_id": body.get("step_id", "")},
            {},
        )
        self._send_json(200, result)

    def _handle_selection(self):
        """현재 Fusion에서 선택된 객체 정보를 반환한다."""
        result = _fire_and_wait("get_selection", {}, {})
        sel = result.get("result", {}).get("selection", [])
        self._send_json(200, {"selection": sel, "count": len(sel)})

    def _handle_show_palette(self):
        """CustomEvent를 발생시켜 메인 스레드에서 팔레트를 표시한다."""
        if _IN_FUSION and _app and _show_palette_event:
            try:
                _app.fireCustomEvent(SHOW_PALETTE_EVENT_ID, '{}')
                self._send_json(200, {"status": "success", "message": "show_palette event fired"})
            except Exception as e:
                self._send_json(200, {"status": "failure", "error": str(e)})
        else:
            self._send_json(200, {"status": "failure", "error": "Not in Fusion or event not registered"})

    def _handle_chat(self, raw: bytes):
        """채팅 메시지를 오케스트레이터(18081)로 중계한다. 선택 정보 포함."""
        import urllib.request
        import urllib.error

        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            self._send_json(400, {"response": "Invalid JSON", "status": "error"})
            return
        message = body.get("message", "").strip()
        if not message:
            self._send_json(400, {"response": "Empty message", "status": "error"})
            return

        # 클라이언트가 보낸 선택 정보 또는 Fusion에서 직접 조회
        selection = body.get("selection")
        if selection is None:
            try:
                sel_result = _fire_and_wait("get_selection", {}, {})
                selection = sel_result.get("result", {}).get("selection", [])
            except Exception:
                selection = []

        # 선택 정보가 있으면 메시지에 포함
        if selection:
            sel_text = json.dumps(selection, ensure_ascii=False)
            message = f"{message}\n\n[선택된 객체: {sel_text}]"

        # 오케스트레이터(18081)로 중계
        try:
            req_data = json.dumps({"message": message}).encode("utf-8")
            req = urllib.request.Request(
                "http://127.0.0.1:18081/api/chat",
                data=req_data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            self._send_json(200, {
                "response": result.get("reply", ""),
                "status": result.get("status", "success"),
            })
        except urllib.error.URLError:
            self._send_json(200, {
                "response": "[오케스트레이터 미연결] python -m orchestrator.core --web 으로 시작하세요",
                "status": "error",
            })
        except Exception as e:
            self._send_json(200, {"response": f"중계 오류: {e}", "status": "error"})

    def _handle_health(self):
        connected = _IN_FUSION and _app is not None
        has_design = False
        if connected:
            try:
                has_design = _app.activeProduct is not None
            except Exception:
                pass

        with _pending_lock:
            pending_count = len(_pending)

        self._send_json(200, {
            "fusion_running": connected,
            "addin_connected": connected,
            "has_active_design": has_design,
            "pending_requests": pending_count,
        })

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _send_json(self, code: int, data: dict):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """서버 로그를 Fusion 텍스트 팔레트로 출력 (또는 무시)."""
        pass


class ThreadedHTTPServer(HTTPServer):
    """요청마다 스레드를 생성하여 동시 요청을 처리한다."""
    allow_reuse_address = True
    daemon_threads = True

    def process_request(self, request, client_address):
        t = threading.Thread(target=self._process, args=(request, client_address), daemon=True)
        t.start()

    def _process(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


def _run_http_server():
    """HTTP 서버를 실행한다 (데몬 스레드)."""
    global _http_server
    try:
        _http_server = ThreadedHTTPServer((HOST, PORT), BridgeHTTPHandler)
        _http_server.serve_forever()
    except Exception:
        pass


# ─── 핸들러 등록 ───


def register_handler(action: str, handler) -> None:
    """커맨드 핸들러를 등록한다."""
    _command_handlers[action] = handler


def _register_all_handlers():
    """모든 내장 + commands/ 모듈의 핸들러를 등록한다."""
    import sys

    # add-in 디렉토리를 sys.path에 추가하여 commands 패키지를 찾을 수 있게 한다
    addin_dir = str(Path(__file__).parent)
    if addin_dir not in sys.path:
        sys.path.insert(0, addin_dir)

    # 내장 핸들러
    register_handler("create_rectangle_sketch", _handle_create_rectangle_sketch)
    register_handler("create_circle_sketch", _handle_create_circle_sketch)
    register_handler("set_material", _handle_set_material)
    register_handler("get_design_info", _handle_get_design_info)
    register_handler("get_body_properties", _handle_get_body_properties)
    register_handler("create_component", _handle_create_component)
    register_handler("fillet", _handle_fillet)
    register_handler("chamfer", _handle_chamfer)
    register_handler("execute_script", _handle_execute_script)
    register_handler("get_selection", _handle_get_selection)

    # commands/ 모듈 핸들러
    _imports = [
        ("commands.sketch", [
            ("handle_create_rectangle_sketch", "create_rectangle_sketch"),
            ("handle_create_circle_sketch", "create_circle_sketch"),
            ("handle_create_sketch", "create_sketch"),  # 하위 호환
        ]),
        ("commands.extrude", [("handle_extrude", "extrude")]),
        ("commands.hole", [("handle_create_hole", "create_hole")]),
        ("commands.pattern", [("handle_rectangular_pattern", "rectangular_pattern")]),
        ("commands.export", [("handle_export_step", "export_step"),
                             ("handle_export_stl", "export_stl")]),
        ("commands.parameter", [("handle_set_parameter", "set_parameter"),
                                ("handle_set_material", "set_material")]),
    ]

    import importlib
    for module_name, handlers in _imports:
        try:
            mod = __import__(module_name, fromlist=[h[0] for h in handlers])
            # 캐시된 모듈이 오래된 경우를 위해 항상 reload
            importlib.reload(mod)
            for func_name, action_name in handlers:
                register_handler(action_name, getattr(mod, func_name))
        except Exception as e:
            # Fusion UI에 표시할 수 있도록 print (Fusion Text Commands 팔레트에 출력됨)
            print(f"[FusionBridge] Failed to import {module_name}: {e}")

    print(f"[FusionBridge] Registered handlers: {sorted(_command_handlers.keys())}")


# ─── Fusion 360 Add-in entry points ───


# GC 방지용 참조 보관
_handlers = []


_PALETTE_ID = 'aiChatPalette'
_PALETTE_NAME = 'AI 설계 어시스턴트'
_PALETTE_WIDTH = 350
_PALETTE_HEIGHT = 500
_CMD_ID = 'aiChatPaletteCmd'
_CMD_NAME = 'AI 설계 어시스턴트'
_CMD_DESC = 'AI 채팅 열기'


def _get_html_url():
    """팔레트 HTML 파일의 file:/// URL을 반환한다."""
    import os
    addin_dir = os.path.dirname(os.path.realpath(__file__))
    html_path = os.path.join(addin_dir, 'chat_palette', 'index.html')
    if not os.path.isfile(html_path):
        return None
    return 'file:///' + html_path.replace('\\', '/')


def _create_palette(ui):
    """팔레트를 생성(또는 재표시)한다."""
    palette = ui.palettes.itemById(_PALETTE_ID)
    if palette:
        palette.isVisible = True
        return palette

    html_url = _get_html_url()
    if not html_url:
        print(f"[FusionBridge] chat_palette/index.html not found")
        return None

    try:
        palette = ui.palettes.add(
            _PALETTE_ID,
            _PALETTE_NAME,
            html_url,
            True,                  # isVisible
            True,                  # showCloseButton
            True,                  # isResizable
            _PALETTE_WIDTH,        # width (350)
            _PALETTE_HEIGHT,       # height (500)
        )
        print(f"[FusionBridge] Palette created: {_PALETTE_WIDTH}x{_PALETTE_HEIGHT}")
    except Exception as e:
        print(f"[FusionBridge] Palette add failed: {e}")
        return None

    try:
        palette.dockingState = adsk.core.PaletteDockingStates.PaletteDockStateRight
    except Exception:
        pass

    return palette


# ─── 팔레트 표시 커맨드 핸들러 ───
#   CommandCreated에서 직접 CustomEvent 발동 (이미 동작 확인된 경로)
#   핸들러는 모듈 최상위 _handlers 에 보관하여 GC 방지


if _IN_FUSION:

    class _ShowPaletteCmdCreatedHandler(adsk.core.CommandCreatedEventHandler):
        """버튼 클릭 → CustomEvent 발동 → 메인 스레드에서 팔레트 표시."""

        def __init__(self):
            super().__init__()

        def notify(self, args):
            try:
                _app.fireCustomEvent(SHOW_PALETTE_EVENT_ID, '{}')
            except Exception:
                pass


def _setup_chat_palette(ui):
    """채팅 팔레트 생성 + 유틸리티 탭 버튼 등록. 실패해도 서버 동작에 영향 없음."""

    # ── 1. 팔레트 생성 ──
    _create_palette(ui)

    # ── 2. 커맨드 + 버튼 등록 ──
    try:
        # 항상 이전 컨트롤과 커맨드 삭제 후 새로 생성
        utilities_tab = ui.allToolbarTabs.itemById('ToolsTab')
        if utilities_tab:
            panel = utilities_tab.toolbarPanels.itemById('SolidScriptsAddinsPanel')
            if panel:
                ctrl = panel.controls.itemById(_CMD_ID)
                if ctrl:
                    ctrl.deleteMe()

        cmd_def = ui.commandDefinitions.itemById(_CMD_ID)
        if cmd_def:
            cmd_def.deleteMe()

        # 새로 생성
        cmd_def = ui.commandDefinitions.addButtonDefinition(
            _CMD_ID, _CMD_NAME, _CMD_DESC)

        on_created = _ShowPaletteCmdCreatedHandler()
        cmd_def.commandCreated.add(on_created)
        _handlers.append(on_created)  # GC 방지

        if utilities_tab:
            panel = utilities_tab.toolbarPanels.itemById('SolidScriptsAddinsPanel')
            if panel:
                panel.controls.addCommand(cmd_def)
                print(f"[FusionBridge] Toolbar button registered")
    except Exception as e:
        print(f"[FusionBridge] Button setup failed: {e}")


def run(context):
    """Add-in start."""
    global _app, _custom_event, _show_palette_event, _stop_flag, _server_thread

    if not _IN_FUSION:
        return

    _app = adsk.core.Application.get()
    ui = _app.userInterface
    _stop_flag = False

    try:
        # 1. 커맨드 핸들러 등록
        _register_all_handlers()

        # 2. CustomEvent 등록 (실행 이벤트)
        _custom_event = _app.registerCustomEvent(CUSTOM_EVENT_ID)
        handler = BridgeEventHandler()
        _custom_event.add(handler)
        _handlers.append(handler)  # GC 방지

        # 2b. ShowPalette CustomEvent 등록 (팔레트 표시 전용)
        _show_palette_event = _app.registerCustomEvent(SHOW_PALETTE_EVENT_ID)
        sp_handler = ShowPaletteEventHandler()
        _show_palette_event.add(sp_handler)
        _handlers.append(sp_handler)  # GC 방지

        # 3. HTTP 서버 시작
        _server_thread = threading.Thread(target=_run_http_server, daemon=True)
        _server_thread.start()

        ui.messageBox(
            f"FusionBridge started.\n"
            f"HTTP server: http://{HOST}:{PORT}\n"
            f"Registered commands: {len(_command_handlers)}"
        )

        # 4. 채팅 Palette + 유틸리티 버튼 (실패해도 서버는 정상 동작)
        _setup_chat_palette(ui)
    except Exception:
        if ui:
            ui.messageBox(f"FusionBridge start failed:\n{traceback.format_exc()}")


def stop(context):
    """Add-in stop."""
    global _stop_flag, _custom_event, _show_palette_event, _http_server

    _stop_flag = True

    # HTTP 서버 종료
    if _http_server is not None:
        try:
            _http_server.shutdown()
        except Exception:
            pass
        _http_server = None

    # 팔레트 + 커맨드 정리
    if _IN_FUSION and _app:
        try:
            ui = _app.userInterface
            palette = ui.palettes.itemById(_PALETTE_ID)
            if palette:
                palette.deleteMe()
        except Exception:
            pass
        try:
            cmd_def = _app.userInterface.commandDefinitions.itemById(_CMD_ID)
            if cmd_def:
                cmd_def.deleteMe()
        except Exception:
            pass

    # CustomEvent 해제
    if _IN_FUSION and _app:
        try:
            _app.unregisterCustomEvent(CUSTOM_EVENT_ID)
        except Exception:
            pass
        _custom_event = None
        try:
            _app.unregisterCustomEvent(SHOW_PALETTE_EVENT_ID)
        except Exception:
            pass
        _show_palette_event = None

    # 대기 중 요청 모두 해제
    with _pending_lock:
        for entry in _pending.values():
            entry["event"].set()
        _pending.clear()

    _handlers.clear()
