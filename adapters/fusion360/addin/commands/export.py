"""내보내기(Export) 커맨드 핸들러 — STEP, STL."""

import traceback

try:
    import adsk.core
    import adsk.fusion
except ImportError:
    pass


def _require_body(root) -> dict | None:
    """bRepBodies가 비어 있으면 에러 dict를 반환, 있으면 None."""
    if root.bRepBodies.count == 0:
        return {
            "status": "failure",
            "error": "바디가 없습니다. 먼저 형상을 생성하세요.",
            "result": {},
        }
    return None


def handle_export_step(parameters: dict, context: dict) -> dict:
    """현재 설계를 STEP AP214로 내보낸다."""
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent

        err = _require_body(root)
        if err:
            return err

        output_path = parameters["output_path"]
        component_id = parameters.get("component_id")

        component = root
        if component_id:
            component = _find_component(root, component_id)

        export_mgr = design.exportManager
        step_options = export_mgr.createSTEPExportOptions(output_path, component)
        export_mgr.execute(step_options)

        # 부피 기록 (변환 검증용)
        total_volume = sum(
            body.physicalProperties.volume for body in component.bRepBodies
        )

        return {
            "status": "success",
            "result": {
                "output_path": output_path,
                "format": "STEP AP214",
                "volume_mm3": total_volume * 1000,
            },
        }
    except Exception:
        return {"status": "failure", "error": traceback.format_exc(), "result": {}}


def handle_export_stl(parameters: dict, context: dict) -> dict:
    """현재 설계를 STL로 내보낸다."""
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent

        err = _require_body(root)
        if err:
            return err

        output_path = parameters["output_path"]
        refinement = parameters.get("refinement", "medium")

        refinement_map = {
            "low": adsk.fusion.MeshRefinementSettings.MeshRefinementLow,
            "medium": adsk.fusion.MeshRefinementSettings.MeshRefinementMedium,
            "high": adsk.fusion.MeshRefinementSettings.MeshRefinementHigh,
        }

        export_mgr = design.exportManager
        stl_options = export_mgr.createSTLExportOptions(root)
        stl_options.meshRefinement = refinement_map.get(
            refinement, adsk.fusion.MeshRefinementSettings.MeshRefinementMedium
        )
        stl_options.filename = output_path
        export_mgr.execute(stl_options)

        return {
            "status": "success",
            "result": {
                "output_path": output_path,
                "refinement": refinement,
            },
        }
    except Exception:
        return {"status": "failure", "error": traceback.format_exc(), "result": {}}


def _find_component(root, component_id: str):
    """컴포넌트를 ID로 찾는다."""
    for occ in root.allOccurrences:
        if occ.component.id == component_id:
            return occ.component
    return root
