"""직사각형 패턴(Rectangular Pattern) 커맨드 핸들러."""

import traceback

try:
    import adsk.core
    import adsk.fusion
except ImportError:
    pass

MM_TO_CM = 0.1


def handle_rectangular_pattern(parameters: dict, context: dict) -> dict:
    """기존 피처를 직사각형 패턴으로 배열한다."""
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent

        # 새 flat 스키마(axis/count/spacing_mm) 또는 기존 스키마(direction1_*) 지원
        d1_axis = parameters.get("axis") or parameters.get("direction1_axis", "x")
        d1_count = parameters.get("count") or parameters.get("direction1_count", 2)
        d1_spacing_cm = (parameters.get("spacing_mm") or parameters.get("direction1_spacing_mm", 10)) * MM_TO_CM
        d2_axis = parameters.get("direction2_axis")
        d2_count = parameters.get("direction2_count", 1)
        d2_spacing_cm = parameters.get("direction2_spacing_mm", 10) * MM_TO_CM if parameters.get("direction2_spacing_mm") else 0

        axis_map = {
            "x": root.xConstructionAxis,
            "y": root.yConstructionAxis,
            "z": root.zConstructionAxis,
        }

        # 최근 피처 가져오기
        timeline = design.timeline
        feature = timeline.item(timeline.count - 1).entity

        patterns = root.features.rectangularPatternFeatures
        entities = adsk.core.ObjectCollection.create()
        entities.add(feature)

        pat_input = patterns.createInput(
            entities,
            axis_map.get(d1_axis, root.xConstructionAxis),
            adsk.core.ValueInput.createByReal(d1_count),
            adsk.core.ValueInput.createByReal(d1_spacing_cm),
            adsk.fusion.PatternDistanceType.SpacingPatternDistanceType,
        )

        if d2_axis and d2_count > 1:
            pat_input.setDirectionTwo(
                axis_map.get(d2_axis, root.yConstructionAxis),
                adsk.core.ValueInput.createByReal(d2_count),
                adsk.core.ValueInput.createByReal(d2_spacing_cm),
            )

        pattern = patterns.add(pat_input)
        total = d1_count * (d2_count if d2_axis else 1)

        return {
            "status": "success",
            "result": {
                "pattern_name": pattern.name,
                "total_instances": total,
            },
        }
    except Exception:
        return {"status": "failure", "error": traceback.format_exc(), "result": {}}
