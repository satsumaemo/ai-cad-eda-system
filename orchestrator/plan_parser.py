"""계획 텍스트 파서 — assistant의 설계 계획에서 실행 단계를 추출한다.

Gemini function calling이 불안정할 때, assistant가 텍스트로 제시한
계획을 파싱하여 어댑터를 직접 호출하는 "direct execution mode"를 지원한다.

흐름:
    1. assistant 메시지에서 단계별 작업을 식별
    2. 각 단계에 해당하는 도구(action)와 파라미터를 매핑
    3. 대화 이력 전체에서 수치 파라미터를 추출
    4. ParsedStep 리스트를 반환
"""

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ParsedStep:
    """파싱된 실행 단계."""
    order: int
    tool_name: str          # 어댑터 접두사 포함: "fusion360__create_rectangle_sketch"
    action: str             # 어댑터 내 액션명: "create_rectangle_sketch"
    adapter: str            # 어댑터명: "fusion360"
    parameters: dict[str, Any]
    description: str        # 사용자에게 보여줄 설명


# ─── 도구 매핑 규칙 ───
# (패턴, adapter, action, description 템플릿)
# 패턴은 우선순위 순서대로 매칭된다.

_TOOL_PATTERNS: list[tuple[list[str], str, str, str]] = [
    # 구 (sphere) — execute_script로 구현
    (
        ["구를 만", "구 생성", "sphere", "구를 생성", "구 형상"],
        "fusion360", "execute_script", "구 생성",
    ),
    # 원형 스케치 (사각형보다 먼저 매칭)
    (
        ["원형 스케치", "circle sketch", "원 스케치", "반원 스케치",
         "원 지름", "원통", "지름", "반지름"],
        "fusion360", "create_circle_sketch", "원형 스케치 생성",
    ),
    # 사각형 스케치
    (
        ["사각형 스케치", "사각 스케치", "rectangle sketch", "스케치를 생성", "스케치 생성",
         "사각형", "직사각형"],
        "fusion360", "create_rectangle_sketch", "스케치 생성",
    ),
    # 돌출
    (
        ["돌출", "extrude", "3d 바디", "3d로"],
        "fusion360", "extrude", "돌출",
    ),
    # 패턴 (홀보다 먼저 — "홀을 패턴 배열"에서 패턴이 우선)
    (
        ["패턴", "pattern", "배열", "격자"],
        "fusion360", "rectangular_pattern", "패턴 배열",
    ),
    # 홀
    (
        ["홀", "hole", "구멍", "관통홀"],
        "fusion360", "create_hole", "홀 생성",
    ),
    # 필렛
    (
        ["필렛", "fillet", "라운드", "모서리를 둥"],
        "fusion360", "fillet", "필렛 적용",
    ),
    # 챔퍼
    (
        ["챔퍼", "chamfer", "모따기"],
        "fusion360", "chamfer", "챔퍼 적용",
    ),
    # 재질
    (
        ["재질", "material", "소재"],
        "fusion360", "set_material", "재질 할당",
    ),
    # ─── 내보내기 ───
    (
        ["step 내보", "step으로 내보", "export step", "step으로 저장",
         "step 파일로 내보", "step파일로"],
        "fusion360", "export_step", "STEP 내보내기",
    ),
    (
        ["stl 내보", "stl 파일", "export stl", "stl으로", "stl로"],
        "fusion360", "export_stl", "STL 내보내기",
    ),
    # 설계 정보
    (
        ["설계 정보", "design info", "조회"],
        "fusion360", "get_design_info", "설계 정보 조회",
    ),
]


# ─── 수치 추출 패턴 ───

# "40mm", "40 mm", "40밀리" 등에서 수치 추출
_NUMBER_UNIT_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:mm|밀리|밀리미터)",
    re.IGNORECASE,
)

# "5mm 돌출" → 5
_EXTRUDE_DISTANCE_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:mm|밀리)?\s*(?:돌출|extrude|높이|깊이|두께)",
    re.IGNORECASE,
)

# "가로 40mm" / "너비 40mm" / "폭 40mm" / "width 40mm"
_WIDTH_PATTERN = re.compile(
    r"(?:가로|너비|폭|width)\s*[:\s]*(\d+(?:\.\d+)?)\s*(?:mm)?",
    re.IGNORECASE,
)

# "세로 20mm" / "높이 20mm" / "height 20mm"
_HEIGHT_PATTERN = re.compile(
    r"(?:세로|높이|height)\s*[:\s]*(\d+(?:\.\d+)?)\s*(?:mm)?",
    re.IGNORECASE,
)

# "세로 20mm" / "height 20mm" — 별도의 _SKETCH_HEIGHT (세로) vs _DEPTH (높이)
_DEPTH_PATTERN = re.compile(
    r"(?:높이|깊이|두께|depth|thickness)\s*[:\s]*(\d+(?:\.\d+)?)\s*(?:mm)?",
    re.IGNORECASE,
)
_SKETCH_HEIGHT_PATTERN = re.compile(
    r"(?:세로)\s*[:\s]*(\d+(?:\.\d+)?)\s*(?:mm)?",
    re.IGNORECASE,
)

# "반지름 10mm" / "radius 10mm" / "R10"
_RADIUS_PATTERN = re.compile(
    r"(?:반지름|반경|radius|r)\s*[:\s]*(\d+(?:\.\d+)?)\s*(?:mm)?",
    re.IGNORECASE,
)

# "Aluminum 6061", "Steel", "ABS", "알루미늄" 등 재질명
_MATERIAL_PATTERN = re.compile(
    r"(?:재질|material|소재)\s*[:=\s]+([A-Za-z][\w\s]*\d*)",
    re.IGNORECASE,
)

# 따옴표로 감싼 재질명: 'Aluminum 6061', "Steel" 등
_MATERIAL_QUOTED_PATTERN = re.compile(
    r"""['"]([A-Za-z][\w\s]*?\d*)['"]""",
    re.IGNORECASE,
)

# 알려진 영문 재질명 — 텍스트에서 직접 매칭
_KNOWN_MATERIALS: list[str] = [
    "Aluminum 6061", "Aluminum 7075", "Aluminum",
    "Stainless Steel", "Steel", "Carbon Steel",
    "Copper", "Brass", "Titanium", "Titanium 6Al-4V",
    "Nylon", "ABS", "PLA", "PETG", "Polycarbonate",
]

# 한국어 재질명 → Fusion 360 재질 매핑
_MATERIAL_KO_MAP: dict[str, str] = {
    "알루미늄": "Aluminum",
    "스테인리스": "Stainless Steel",
    "스틸": "Steel",
    "구리": "Copper",
    "티타늄": "Titanium",
    "황동": "Brass",
    "나일론": "Nylon",
    "abs": "ABS",
    "pla": "PLA",
}

# "직경 5mm" / "지름 5mm" / "diameter 5mm" / "ø5"
_DIAMETER_PATTERN = re.compile(
    r"(?:직경|지름|diameter|ø)\s*[:\s]*(\d+(?:\.\d+)?)\s*(?:mm)?",
    re.IGNORECASE,
)

# 필렛/챔퍼 반지름: "필렛 2mm" 또는 "2mm 필렛" 양방향 매칭
_FILLET_RADIUS_PATTERN = re.compile(
    r"(?:(?:필렛|fillet|라운드)\s*[:\s(]*(\d+(?:\.\d+)?)\s*(?:mm)?|(\d+(?:\.\d+)?)\s*(?:mm)?\s*(?:필렛|fillet|라운드))",
    re.IGNORECASE,
)

_CHAMFER_DISTANCE_PATTERN = re.compile(
    r"(?:(?:챔퍼|chamfer|모따기)\s*[:\s(]*(\d+(?:\.\d+)?)\s*(?:mm)?|(\d+(?:\.\d+)?)\s*(?:mm)?\s*(?:챔퍼|chamfer|모따기))",
    re.IGNORECASE,
)

# "40mm x 40mm x 5mm", "40x40x5mm", "40*40*5mm", "4cm x 4cm x 0.5cm" 등 박스 치수 패턴
_BOX_DIM_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:mm|cm|inch|in|인치)?\s*[x×*]\s*"
    r"(\d+(?:\.\d+)?)\s*(?:mm|cm|inch|in|인치)?\s*[x×*]\s*"
    r"(\d+(?:\.\d+)?)\s*(?:mm|cm|inch|in|인치)?",
    re.IGNORECASE,
)

# 단위 변환 계수 → mm
_UNIT_TO_MM: dict[str, float] = {
    "mm": 1.0,
    "cm": 10.0,
    "inch": 25.4,
    "in": 25.4,
    "인치": 25.4,
}

# 텍스트에서 사용된 단위를 감지
_UNIT_DETECT_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(cm|inch|in|인치|mm|밀리)",
    re.IGNORECASE,
)


def _extract_all_text(conversation: list[dict[str, Any]]) -> str:
    """대화 이력 전체에서 텍스트를 추출한다."""
    texts: list[str] = []
    for msg in conversation:
        content = msg.get("content", "")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for p in content:
                if isinstance(p, dict):
                    texts.append(p.get("text", ""))
        parts = msg.get("parts", [])
        if isinstance(parts, list):
            for p in parts:
                if isinstance(p, dict):
                    texts.append(p.get("text", ""))
    return " ".join(texts)


def _get_last_assistant_text(conversation: list[dict[str, Any]]) -> str:
    """직전 assistant 메시지의 텍스트를 반환한다."""
    for msg in reversed(conversation):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                p.get("text", "") for p in content if isinstance(p, dict)
            )
        parts = msg.get("parts", [])
        if isinstance(parts, list):
            return " ".join(
                p.get("text", "") for p in parts if isinstance(p, dict)
            )
    return ""


def _detect_unit_scale(text: str) -> float:
    """텍스트에서 사용된 단위를 감지하여 mm 변환 계수를 반환한다."""
    m = _UNIT_DETECT_PATTERN.search(text)
    if m:
        unit = m.group(1).lower()
        return _UNIT_TO_MM.get(unit, 1.0)
    return 1.0  # 단위 미표기 시 mm 기본


def _extract_box_dims(text: str) -> tuple[float, float, float] | None:
    """'AxBxC' 형식의 박스 치수를 추출한다. (width, height, depth) 단위는 mm로 변환."""
    m = _BOX_DIM_PATTERN.search(text)
    if m:
        scale = _detect_unit_scale(text)
        return (
            float(m.group(1)) * scale,
            float(m.group(2)) * scale,
            float(m.group(3)) * scale,
        )
    return None


def _extract_2d_dims(text: str) -> tuple[float, float] | None:
    """'AxB' 형식의 2차원 치수를 추출한다. (width, height)"""
    m = re.search(
        r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*[x×*]\s*(\d+(?:\.\d+)?)\s*(?:mm)?",
        text, re.IGNORECASE,
    )
    if m:
        scale = _detect_unit_scale(text)
        return float(m.group(1)) * scale, float(m.group(2)) * scale
    return None


def _extract_params_for_action(
    action: str,
    full_text: str,
) -> dict[str, Any]:
    """액션에 맞는 파라미터를 대화 텍스트에서 추출한다."""
    params: dict[str, Any] = {}
    text_lower = full_text.lower()

    # AxBxC 박스 치수 사전 추출 (스케치 + 돌출에 공유)
    box_dims = _extract_box_dims(full_text)

    if action == "create_rectangle_sketch":
        params["plane"] = "xy"
        # 평면 감지
        if "xz" in text_lower:
            params["plane"] = "xz"
        elif "yz" in text_lower:
            params["plane"] = "yz"

        params["x_mm"] = 0
        params["y_mm"] = 0

        # 1순위: AxBxC 박스 패턴 — 처음 두 값이 스케치 치수
        if box_dims:
            params["width_mm"] = box_dims[0]
            params["height_mm"] = box_dims[1]
        else:
            # 2순위: AxB 2차원 패턴 ("50x30mm")
            dims_2d = _extract_2d_dims(full_text)
            if dims_2d:
                params["width_mm"] = dims_2d[0]
                params["height_mm"] = dims_2d[1]
            else:
                w_match = _WIDTH_PATTERN.search(full_text)
                sh_match = _SKETCH_HEIGHT_PATTERN.search(full_text)
                h_match = _HEIGHT_PATTERN.search(full_text)

                if w_match:
                    params["width_mm"] = float(w_match.group(1))
                if sh_match:
                    params["height_mm"] = float(sh_match.group(1))
                elif h_match and "width_mm" not in params:
                    params["height_mm"] = float(h_match.group(1))

                # width/height가 없으면 일반 수치에서 추출 시도
                if "width_mm" not in params or "height_mm" not in params:
                    numbers = _NUMBER_UNIT_PATTERN.findall(full_text)
                    nums = [float(n) for n in numbers]
                    if len(nums) >= 2 and "width_mm" not in params:
                        params["width_mm"] = nums[0]
                        params["height_mm"] = nums[1]
                    elif len(nums) == 1 and "width_mm" not in params:
                        params["width_mm"] = nums[0]
                        params["height_mm"] = nums[0]

    elif action == "create_circle_sketch":
        params["plane"] = "xy"
        if "xz" in text_lower:
            params["plane"] = "xz"
        elif "yz" in text_lower:
            params["plane"] = "yz"
        params["center_x_mm"] = 0
        params["center_y_mm"] = 0
        r_match = _RADIUS_PATTERN.search(full_text)
        if r_match:
            params["radius_mm"] = float(r_match.group(1))
        else:
            d_match = _DIAMETER_PATTERN.search(full_text)
            if d_match:
                params["radius_mm"] = float(d_match.group(1)) / 2

    elif action == "extrude":
        # 1순위: AxBxC 박스 패턴 — 세 번째 값이 돌출 거리
        if box_dims:
            params["distance_mm"] = box_dims[2]
        else:
            # 2순위: "높이/깊이/두께 Xmm" (가로/세로와 함께 쓰인 경우)
            depth_match = _DEPTH_PATTERN.search(full_text)
            if depth_match:
                params["distance_mm"] = float(depth_match.group(1))
            else:
                # 3순위: "Xmm 돌출" 패턴
                dist_match = _EXTRUDE_DISTANCE_PATTERN.search(full_text)
                if dist_match:
                    params["distance_mm"] = float(dist_match.group(1))
                else:
                    # 4순위: 일반 수치에서 스케치 치수와 다른 값 찾기
                    numbers = [float(n) for n in _NUMBER_UNIT_PATTERN.findall(full_text)]
                    if numbers:
                        params["distance_mm"] = min(numbers)

    elif action == "set_material":
        material_name = ""

        # 1순위: 한국어 재질명 직접 매칭
        for ko_name, en_name in _MATERIAL_KO_MAP.items():
            if ko_name in text_lower:
                material_name = en_name
                break

        # 2순위: 따옴표로 감싼 영문 재질명 ('Aluminum 6061' 등)
        if not material_name:
            quoted = _MATERIAL_QUOTED_PATTERN.search(full_text)
            if quoted:
                material_name = quoted.group(1).strip()

        # 3순위: 알려진 재질명 직접 매칭 (대소문자 무시)
        if not material_name:
            ft_lower = full_text.lower()
            for known in _KNOWN_MATERIALS:
                if known.lower() in ft_lower:
                    material_name = known
                    break

        # 4순위: "재질: Aluminum" 패턴 (영문 시작만 캡처)
        if not material_name:
            mat_match = _MATERIAL_PATTERN.search(full_text)
            if mat_match:
                raw = mat_match.group(1).strip()
                if raw and raw[0].isascii() and raw[0].isalpha():
                    material_name = raw

        # 기본값
        if not material_name:
            material_name = "Aluminum 6061"
            logger.info("Material name not found, using default: %s", material_name)

        params["material_name"] = material_name
        # body_id는 실행 시 직전 결과에서 가져옴
        params["body_id"] = ""  # 플레이스홀더 — 실행 시 채움

    elif action == "fillet":
        f_match = _FILLET_RADIUS_PATTERN.search(full_text)
        if f_match:
            # 양방향 매칭: group(1) 또는 group(2)
            val = f_match.group(1) or f_match.group(2)
            params["radius_mm"] = float(val)
        params["all_edges"] = True  # 기본적으로 모든 모서리

    elif action == "chamfer":
        f_match = _CHAMFER_DISTANCE_PATTERN.search(full_text)
        if f_match:
            val = f_match.group(1) or f_match.group(2)
            params["distance_mm"] = float(val)
        params["all_edges"] = True

    elif action == "rectangular_pattern":
        # "x방향 4개 15mm 간격, y방향 4개 15mm 간격" 파싱
        count_pattern = re.compile(r"(\d+)\s*(?:개|ea)", re.IGNORECASE)
        spacing_pattern = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*(?:간격|spacing)", re.IGNORECASE)

        counts = count_pattern.findall(full_text)
        spacings = spacing_pattern.findall(full_text)

        if counts:
            params["direction1_count"] = int(counts[0])
        else:
            params["direction1_count"] = 3
        if spacings:
            params["direction1_spacing_mm"] = float(spacings[0])
        else:
            params["direction1_spacing_mm"] = 10

        params["direction1_axis"] = "x"

        if len(counts) >= 2:
            params["direction2_axis"] = "y"
            params["direction2_count"] = int(counts[1])
            params["direction2_spacing_mm"] = float(spacings[1]) if len(spacings) >= 2 else params["direction1_spacing_mm"]

    elif action == "create_hole":
        d_match = _DIAMETER_PATTERN.search(full_text)
        if d_match:
            params["diameter_mm"] = float(d_match.group(1))
        else:
            # "M5", "M10" 등 규격 볼트 지름
            m_match = re.search(r"M(\d+(?:\.\d+)?)", full_text)
            if m_match:
                params["diameter_mm"] = float(m_match.group(1))
        params["face_id"] = "top"  # 상단면 자동 탐색
        params["center_x_mm"] = 0  # 면 중심 기준 오프셋 (0 = 정중앙)
        params["center_y_mm"] = 0
        params["through_all"] = True  # 기본: 관통홀

    elif action == "execute_script":
        # 구(sphere) 생성 스크립트
        d_match = _DIAMETER_PATTERN.search(full_text)
        r_match = _RADIUS_PATTERN.search(full_text)
        if d_match:
            radius_mm = float(d_match.group(1)) / 2
        elif r_match:
            radius_mm = float(r_match.group(1))
        else:
            numbers = [float(n) for n in _NUMBER_UNIT_PATTERN.findall(full_text)]
            radius_mm = numbers[0] / 2 if numbers else 25.0

        radius_cm = radius_mm * 0.1
        # 반원(semicircle) 프로파일 + 360도 revolve로 구 생성
        # 호(arc)로 반원 + 직선(line)으로 닫기 → 닫힌 프로파일 생성
        # 닫는 직선을 revolve 축으로 사용
        params["script_code"] = (
            "import adsk.core, adsk.fusion, math, traceback\n"
            "def run(ctx):\n"
            "    try:\n"
            "        app = adsk.core.Application.get()\n"
            "        design = adsk.fusion.Design.cast(app.activeProduct)\n"
            "        root = design.rootComponent\n"
            f"        r = {radius_cm}\n"
            "        sketch = root.sketches.add(root.xZConstructionPlane)\n"
            "        lines = sketch.sketchCurves.sketchLines\n"
            "        arcs = sketch.sketchCurves.sketchArcs\n"
            "        p_top = adsk.core.Point3D.create(0, r, 0)\n"
            "        p_bot = adsk.core.Point3D.create(0, -r, 0)\n"
            "        p_mid = adsk.core.Point3D.create(r, 0, 0)\n"
            "        arc = arcs.addByThreePoints(p_top, p_mid, p_bot)\n"
            "        closing_line = lines.addByTwoPoints(\n"
            "            arc.startSketchPoint, arc.endSketchPoint)\n"
            "        prof = sketch.profiles.item(0)\n"
            "        revolves = root.features.revolveFeatures\n"
            "        rev_input = revolves.createInput(prof,\n"
            "            closing_line, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)\n"
            "        angle = adsk.core.ValueInput.createByString('360 deg')\n"
            "        rev_input.setAngleExtent(False, angle)\n"
            "        rev = revolves.add(rev_input)\n"
            "        body = rev.bodies.item(0)\n"
            "        props = body.physicalProperties\n"
            "        return {'status': 'success', 'result': {\n"
            "            'body_name': body.name,\n"
            f"            'radius_mm': {radius_mm},\n"
            "            'volume_mm3': props.volume * 1000\n"
            "        }}\n"
            "    except Exception:\n"
            "        return {'status': 'failure', 'error': traceback.format_exc(), 'result': {}}\n"
        )

    elif action == "export_step":
        import os
        base = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))
        out_dir = os.path.join(base, "results")
        os.makedirs(out_dir, exist_ok=True)
        params["output_path"] = os.path.abspath(os.path.join(out_dir, "design.step"))

    elif action == "export_stl":
        import os
        base = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"))
        out_dir = os.path.join(base, "results")
        os.makedirs(out_dir, exist_ok=True)
        params["output_path"] = os.path.abspath(os.path.join(out_dir, "design.stl"))

    elif action == "get_design_info":
        params["include_parameters"] = True
        params["include_bodies"] = True

    return params


def parse_plan_from_conversation(
    conversation: list[dict[str, Any]],
) -> list[ParsedStep]:
    """대화 이력에서 실행 계획을 파싱한다.

    직전 assistant 메시지의 계획 텍스트에서 단계를 식별하고,
    대화 전체에서 파라미터를 추출한다.

    Returns:
        ParsedStep 리스트 (실행 순서대로)
    """
    assistant_text = _get_last_assistant_text(conversation)
    if not assistant_text:
        logger.warning("No assistant text found for plan parsing")
        return []

    full_text = _extract_all_text(conversation)
    assistant_lower = assistant_text.lower()

    steps: list[ParsedStep] = []

    # 번호가 매겨진 단계를 줄 단위로 분석
    # "1. 스케치를 생성", "2. 돌출", "3. 재질" 등
    lines = assistant_text.split("\n")
    numbered_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if re.match(r"^[\d①②③④⑤⑥⑦⑧⑨⑩]+[.\))\-:\s]", stripped):
            numbered_lines.append(stripped)

    # 번호 매겨진 줄이 있으면 각 줄을 독립적으로 매칭 (동일 action 반복 허용)
    if numbered_lines:
        for line_text in numbered_lines:
            line_lower = line_text.lower()

            # 한 줄에 "스케치 + 돌출"이 같이 있으면 두 단계로 분리
            has_sketch = any(p in line_lower for p in ["스케치", "sketch", "사각형", "원형"])
            has_extrude = any(p in line_lower for p in ["돌출", "extrude"])
            if has_sketch and has_extrude:
                # 스케치 단계
                sk_params = _extract_params_for_action("create_rectangle_sketch", line_text)
                if not sk_params.get("width_mm"):
                    sk_params = _extract_params_for_action("create_rectangle_sketch", full_text)
                # join 여부: 번호가 1이 아니면 join
                is_first = (len(steps) == 0)
                steps.append(ParsedStep(
                    order=len(steps) + 1,
                    tool_name="fusion360__create_rectangle_sketch",
                    action="create_rectangle_sketch",
                    adapter="fusion360",
                    parameters=sk_params,
                    description=f"스케치 ({line_text.strip()[:30]})",
                ))
                # 돌출 단계
                ex_params = _extract_params_for_action("extrude", line_text)
                if not ex_params.get("distance_mm"):
                    ex_params = _extract_params_for_action("extrude", full_text)
                if not is_first:
                    ex_params.setdefault("operation", "join")
                steps.append(ParsedStep(
                    order=len(steps) + 1,
                    tool_name="fusion360__extrude",
                    action="extrude",
                    adapter="fusion360",
                    parameters=ex_params,
                    description=f"돌출 ({line_text.strip()[:30]})",
                ))
                continue

            for patterns, adapter, action, desc in _TOOL_PATTERNS:
                if any(p in line_lower for p in patterns):
                    params = _extract_params_for_action(action, line_text)
                    if action == "extrude" and "distance_mm" not in params:
                        params = _extract_params_for_action(action, full_text)
                    if action in ("create_rectangle_sketch",) and "width_mm" not in params:
                        fallback = _extract_params_for_action(action, full_text)
                        for k, v in fallback.items():
                            params.setdefault(k, v)
                    if action == "create_circle_sketch" and "radius_mm" not in params:
                        fallback = _extract_params_for_action(action, full_text)
                        for k, v in fallback.items():
                            params.setdefault(k, v)
                    if action in ("run_structural_analysis", "run_thermal_analysis"):
                        params = _extract_params_for_action(action, full_text)
                    steps.append(ParsedStep(
                        order=len(steps) + 1,
                        tool_name=f"{adapter}__{action}",
                        action=action,
                        adapter=adapter,
                        parameters=params,
                        description=f"{desc} ({line_text.strip()[:40]})",
                    ))
                    break
    else:
        # 번호 없는 경우: 전체 텍스트에서 패턴 순서대로 매칭 (중복 불가)
        matched_actions: set[str] = set()
        for patterns, adapter, action, desc in _TOOL_PATTERNS:
            if action in matched_actions:
                continue
            if any(p in assistant_lower for p in patterns):
                params = _extract_params_for_action(action, full_text)
                steps.append(ParsedStep(
                    order=len(steps) + 1,
                    tool_name=f"{adapter}__{action}",
                    action=action,
                    adapter=adapter,
                    parameters=params,
                    description=desc,
                ))
                matched_actions.add(action)

    if steps:
        logger.info(
            "Parsed %d plan steps: %s",
            len(steps),
            [f"{s.order}.{s.action}" for s in steps],
        )
    else:
        logger.warning("No plan steps could be parsed from assistant text")

    return steps




def _infer_fillet_chamfer(recent_text: str) -> list[ParsedStep]:
    """필렛/챔퍼 요청을 파싱한다.

    "2mm 필렛", "필렛 2mm", "이 면에 3mm 챔퍼" 등
    선택 정보([선택된 객체: ...])가 있으면 edge_ids를 추출한다.
    """
    rt_lower = recent_text.lower()

    is_fillet = any(kw in rt_lower for kw in ["필렛", "fillet", "라운드", "모서리를 둥"])
    is_chamfer = any(kw in rt_lower for kw in ["챔퍼", "chamfer", "모따기"])

    if not is_fillet and not is_chamfer:
        return []

    # 반지름/거리 추출
    # "2mm 필렛" 또는 "필렛 2mm" 양방향
    val = 2.0  # 기본값
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:mm)?\s*(?:필렛|fillet|챔퍼|chamfer|라운드|모따기)", recent_text, re.IGNORECASE)
    if not m:
        m = re.search(r"(?:필렛|fillet|챔퍼|chamfer|라운드|모따기)\s*(\d+(?:\.\d+)?)\s*(?:mm)?", recent_text, re.IGNORECASE)
    if m:
        val = float(m.group(1))

    # 선택 정보에서 edge ID 추출
    edge_ids: list[str] = []
    sel_match = re.search(r"\[선택된 객체:\s*(\[.*?\])\]", recent_text, re.DOTALL)
    if sel_match:
        try:
            import json as _json
            sel_data = _json.loads(sel_match.group(1))
            for item in sel_data:
                if "Edge" in item.get("type", ""):
                    # 엣지 직접 선택
                    edge_ids.append(item.get("id", ""))
                elif "Face" in item.get("type", ""):
                    # 면 선택 → 해당 면의 모든 엣지 ID 사용
                    face_edges = item.get("edge_ids", [])
                    edge_ids.extend(face_edges)
        except Exception:
            pass

    all_edges = not edge_ids  # 엣지가 없으면 모든 모서리
    if edge_ids:
        logger.info("[infer_fillet] 선택된 엣지 %d개: %s", len(edge_ids), edge_ids[:5])

    if is_fillet:
        logger.info("[infer_fillet] radius=%.1fmm, all_edges=%s, edge_ids=%d개", val, all_edges, len(edge_ids))
        params: dict[str, Any] = {"radius_mm": val, "all_edges": all_edges}
        if edge_ids:
            params["edge_ids"] = edge_ids
        return [ParsedStep(
            order=1,
            tool_name="fusion360__fillet",
            action="fillet",
            adapter="fusion360",
            parameters=params,
            description=f"R{val}mm 필렛",
        )]
    else:
        logger.info("[infer_chamfer] distance=%.1fmm, all_edges=%s", val, all_edges)
        params = {"distance_mm": val, "all_edges": all_edges}
        if edge_ids:
            params["edge_ids"] = edge_ids
        return [ParsedStep(
            order=1,
            tool_name="fusion360__chamfer",
            action="chamfer",
            adapter="fusion360",
            parameters=params,
            description=f"{val}mm 챔퍼",
        )]


def _infer_holes(recent_text: str, full_text: str) -> list[ParsedStep]:
    """홀 요청을 파싱하여 create_hole 단계를 생성한다.

    "M3 홀 2개, 간격 30mm, 중앙 정렬" → 2개의 create_hole 단계
    위치는 바디 중앙 기준으로 간격만큼 균등 배치한다.
    """
    rt_lower = recent_text.lower()

    # 홀 키워드 확인 (최근 컨텍스트에서)
    if not any(kw in rt_lower for kw in ["홀", "hole", "구멍", "관통홀"]):
        return []

    # 직경 추출: "M3" → 3mm, "M4" → 4mm, "직경 5mm"
    diameter = 5.0  # 기본값
    m_match = re.search(r"[Mm](\d+(?:\.\d+)?)", recent_text)
    d_match = re.search(r"(?:직경|지름|diameter)\s*[:=]?\s*(\d+(?:\.\d+)?)", recent_text, re.IGNORECASE)
    if m_match:
        diameter = float(m_match.group(1))
    elif d_match:
        diameter = float(d_match.group(1))

    # 개수 추출: "2개", "3개"
    count = 1
    count_match = re.search(r"(\d+)\s*개", recent_text)
    if count_match:
        count = int(count_match.group(1))
    count = min(count, 10)  # 최대 10개

    # 간격 추출: "간격 30mm"
    spacing = 0.0
    spacing_match = re.search(r"간격\s*(\d+(?:\.\d+)?)\s*(?:mm)?", recent_text, re.IGNORECASE)
    if spacing_match:
        spacing = float(spacing_match.group(1))

    # 관통 여부
    through_all = any(kw in rt_lower for kw in ["관통", "through"])

    # 깊이 (관통이 아닌 경우)
    depth = None
    if not through_all:
        depth_match = re.search(r"(?:깊이|depth)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:mm)?", recent_text, re.IGNORECASE)
        if depth_match:
            depth = float(depth_match.group(1))
        else:
            through_all = True  # 깊이 미지정이면 관통

    logger.info("[infer_holes] diameter=%.1fmm, count=%d, spacing=%.1fmm, through=%s",
                diameter, count, spacing, through_all)

    # 위치 계산: 면 중심(0,0) 기준 오프셋
    # 간격이 있으면 X방향 균등 배치, 없으면 중앙에 1개
    offsets: list[tuple[float, float]] = []
    if count == 1:
        offsets.append((0.0, 0.0))
    elif spacing > 0:
        # 중앙 정렬: 총 span = spacing * (count-1), 시작 = -span/2
        total_span = spacing * (count - 1)
        start_x = -total_span / 2
        for i in range(count):
            offsets.append((start_x + spacing * i, 0.0))
    else:
        # 간격 미지정: X방향 10mm 간격
        total_span = 10.0 * (count - 1)
        start_x = -total_span / 2
        for i in range(count):
            offsets.append((start_x + 10.0 * i, 0.0))

    steps: list[ParsedStep] = []
    for i, (ox, oy) in enumerate(offsets):
        steps.append(ParsedStep(
            order=i + 1,
            tool_name="fusion360__create_hole",
            action="create_hole",
            adapter="fusion360",
            parameters={
                "face_id": "top",
                "center_x_mm": ox,
                "center_y_mm": oy,
                "diameter_mm": diameter,
                "depth_mm": depth,
                "through_all": through_all,
            },
            description=f"M{diameter:.0f} 홀 #{i+1} (offset {ox:+.1f}, {oy:+.1f}mm)",
        ))

    return steps


def infer_plan_from_conversation(conversation: list[dict[str, Any]]) -> list[ParsedStep]:
    """plan_parser가 실패했을 때, 대화 전체 텍스트에서 치수를 추출하여 기본 계획을 생성한다.

    "40x40x5mm 박스" → 스케치(40x40) + 돌출(5mm)
    "반지름 20mm 원기둥 높이 30mm" → 원형 스케치(r=20) + 돌출(30mm)
    """
    full_text = _extract_all_text(conversation)

    # 최근 컨텍스트 (마지막 사용자 요청 + 마지막 AI 계획)만 키워드 감지에 사용
    # 전체 이력은 파라미터(치수) 추출에만 사용
    recent_parts: list[str] = []
    for msg in reversed(conversation):
        content = msg.get("content", "")
        if isinstance(content, str) and content.strip():
            recent_parts.append(content)
        if len(recent_parts) >= 3:  # 마지막 user + assistant + user(승인)
            break
    recent_text = " ".join(reversed(recent_parts))
    rt_lower = recent_text.lower()

    logger.debug("[infer_plan] recent_text: %s", recent_text[:200])

    # ─── 필렛/챔퍼 (기존 바디 수정) ───
    fillet_steps = _infer_fillet_chamfer(recent_text)
    if fillet_steps:
        return fillet_steps

    # ─── 홀 (기존 바디에 추가 작업) ───
    hole_steps = _infer_holes(recent_text, full_text)
    if hole_steps:
        return hole_steps

    # ─── STEP/STL 내보내기 ───
    is_export_step = any(kw in rt_lower for kw in [
        "step 내보", "step으로 내보", "export step", "step으로 저장",
        "step 파일로 내보", "step파일로",
    ])
    is_export_stl = any(kw in rt_lower for kw in [
        "stl 내보", "stl 파일", "export stl", "stl으로", "stl로",
    ])
    if is_export_step or is_export_stl:
        steps: list[ParsedStep] = []
        if is_export_step:
            steps.append(ParsedStep(
                order=len(steps) + 1,
                tool_name="fusion360__export_step",
                action="export_step",
                adapter="fusion360",
                parameters=_extract_params_for_action("export_step", full_text),
                description="STEP 내보내기",
            ))
        if is_export_stl:
            steps.append(ParsedStep(
                order=len(steps) + 1,
                tool_name="fusion360__export_stl",
                action="export_stl",
                adapter="fusion360",
                parameters=_extract_params_for_action("export_stl", full_text),
                description="STL 내보내기",
            ))
        return steps

    # ─── 복잡/복합 형상은 LLM conversation loop에 위임 ───
    # L자 브래킷, 방열판, 인클로저 등은 AI가 기본 도구를 조합하여 생성
    _CONVERSATION_LOOP_KEYWORDS = [
        "l자", "l-자", "l 자", "ㄱ자", "ㄴ자",
        "t자", "t-자", "u자",
        "방열판", "heatsink", "heat sink",
        "인클로저", "enclosure", "케이스",
        "쉘", "shell", "속 비우", "속을 비우",
        "폴리라인", "polyline", "다각형", "polygon",
        "리브", "rib", "보스", "boss", "포켓", "pocket",
    ]
    if any(kw in rt_lower for kw in _CONVERSATION_LOOP_KEYWORDS):
        logger.info("[infer_plan] 복합 형상 감지 — conversation loop에 위임")
        return []

    # ─── 단순 박스 형상 — 최근 컨텍스트에서 치수 검색 ───

    # 박스 패턴: WxHxD (최근 컨텍스트에서만)
    box_m = _BOX_DIM_PATTERN.search(recent_text)
    if box_m:
        w, h, d = float(box_m.group(1)), float(box_m.group(2)), float(box_m.group(3))
        unit_m = _UNIT_DETECT_PATTERN.search(recent_text)
        if unit_m:
            factor = _UNIT_TO_MM.get(unit_m.group(1).lower(), 1.0)
            w, h, d = w * factor, h * factor, d * factor

        steps: list[ParsedStep] = [
            ParsedStep(
                order=1,
                tool_name="fusion360__create_rectangle_sketch",
                action="create_rectangle_sketch",
                adapter="fusion360",
                parameters={"plane": "xy", "x_mm": 0, "y_mm": 0, "width_mm": w, "height_mm": h},
                description=f"{w}x{h}mm 스케치 생성",
            ),
            ParsedStep(
                order=2,
                tool_name="fusion360__extrude",
                action="extrude",
                adapter="fusion360",
                parameters={"distance_mm": d},
                description=f"{d}mm 돌출",
            ),
        ]

        logger.info("[infer_plan] 단순 박스 감지: %.1f x %.1f x %.1fmm", w, h, d)

        return steps

    # 원기둥 패턴 (최근 컨텍스트에서만)
    radius_m = re.search(r"(?:반지름|radius|r)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:mm)?", recent_text, re.IGNORECASE)
    diameter_m = re.search(r"(?:지름|직경|diameter|d)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:mm)?", recent_text, re.IGNORECASE)
    height_m = re.search(r"(?:높이|height|h)\s*[:=]?\s*(\d+(?:\.\d+)?)\s*(?:mm)?", recent_text, re.IGNORECASE)
    r_val = None
    if radius_m:
        r_val = float(radius_m.group(1))
    elif diameter_m:
        r_val = float(diameter_m.group(1)) / 2
    if r_val and height_m:
        h_val = float(height_m.group(1))
        logger.info("[infer_plan] 원기둥만 감지: r=%.1fmm, h=%.1fmm", r_val, h_val)
        return [
            ParsedStep(
                order=1,
                tool_name="fusion360__create_circle_sketch",
                action="create_circle_sketch",
                adapter="fusion360",
                parameters={"plane": "xy", "center_x_mm": 0, "center_y_mm": 0, "radius_mm": r_val},
                description=f"r={r_val}mm 원형 스케치",
            ),
            ParsedStep(
                order=2,
                tool_name="fusion360__extrude",
                action="extrude",
                adapter="fusion360",
                parameters={"distance_mm": h_val},
                description=f"{h_val}mm 돌출",
            ),
        ]

    logger.warning("[infer_plan] 대화에서 형상/해석 패턴을 추출하지 못함")
    return []


def enrich_step_params(
    step: ParsedStep,
    previous_results: list[dict[str, Any]],
) -> ParsedStep:
    """직전 단계의 결과를 사용해 현재 단계의 파라미터를 보강한다.

    예: set_material의 body_id를 직전 extrude 결과에서 가져오기.
    """
    if not previous_results:
        return step

    last_result = previous_results[-1]
    result_data = last_result.get("result", last_result)

    # extrude: 이전에 extrude가 있었으면 join, 처음이면 new_body
    if step.action == "extrude" and "operation" not in step.parameters:
        has_prior_extrude = any(
            r.get("result", r).get("body_name") or r.get("result", r).get("volume_mm3")
            for r in previous_results
        )
        step.parameters["operation"] = "join" if has_prior_extrude else "new_body"

    if step.action == "set_material" and not step.parameters.get("body_id"):
        # 직전 결과에서 body 이름/ID 추출
        body_name = (
            result_data.get("body_name")
            or result_data.get("body_id")
            or result_data.get("result", {}).get("body_name", "")
        )
        if body_name:
            step.parameters["body_id"] = body_name
            logger.debug("Enriched set_material body_id=%s from previous result", body_name)

    if step.action == "create_hole" and not step.parameters.get("face_id"):
        face_id = result_data.get("face_id", result_data.get("result", {}).get("face_id", ""))
        if face_id:
            step.parameters["face_id"] = face_id

    return step
