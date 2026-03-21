"""돌출(Extrude) 커맨드 핸들러."""

import traceback

try:
    import adsk.core
    import adsk.fusion
except ImportError:
    pass

MM_TO_CM = 0.1

OPERATION_MAP = {
    "new_body": "NewBodyFeatureOperation",
    "join": "JoinFeatureOperation",
    "cut": "CutFeatureOperation",
    "intersect": "IntersectFeatureOperation",
}


def handle_extrude(parameters: dict, context: dict) -> dict:
    """프로파일을 돌출하여 3D 바디를 생성한다."""
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent

        distance_mm = parameters.get("distance_mm", 10)
        distance_cm = distance_mm * MM_TO_CM
        profile_index = parameters.get("profile_index", 0)
        operation = parameters.get("operation", "new_body")
        direction = parameters.get("direction", "positive")

        # 최근 스케치의 프로파일
        if root.sketches.count == 0:
            return {"status": "failure", "error": "스케치가 없습니다. 먼저 스케치를 생성하세요.", "result": {}}
        sketch = root.sketches.item(root.sketches.count - 1)
        if sketch.profiles.count == 0:
            return {"status": "failure", "error": f"스케치 '{sketch.name}'에 프로파일이 없습니다.", "result": {}}
        prof = sketch.profiles.item(profile_index)

        op = getattr(adsk.fusion.FeatureOperations, OPERATION_MAP.get(operation, "NewBodyFeatureOperation"))
        extrudes = root.features.extrudeFeatures

        if direction == "positive":
            distance = adsk.core.ValueInput.createByReal(distance_cm)
            ext = extrudes.addSimple(prof, distance, op)
        else:
            ext_input = extrudes.createInput(prof, op)
            dist_def = adsk.fusion.DistanceExtentDefinition.create(
                adsk.core.ValueInput.createByReal(distance_cm)
            )
            if direction == "symmetric":
                ext_input.setSymmetricExtent(
                    adsk.core.ValueInput.createByReal(distance_cm), True
                )
            elif direction == "negative":
                ext_input.setOneSideExtent(
                    dist_def, adsk.fusion.ExtentDirections.NegativeExtentDirection
                )
            ext = extrudes.add(ext_input)

        body = ext.bodies.item(0)
        props = body.physicalProperties
        bbox = body.boundingBox

        return {
            "status": "success",
            "result": {
                "body_name": body.name,
                "volume_mm3": props.volume * 1000,
                "surface_area_mm2": props.area * 100,
                "bounding_box": {
                    "min": [bbox.minPoint.x * 10, bbox.minPoint.y * 10, bbox.minPoint.z * 10],
                    "max": [bbox.maxPoint.x * 10, bbox.maxPoint.y * 10, bbox.maxPoint.z * 10],
                },
            },
        }
    except Exception:
        return {"status": "failure", "error": traceback.format_exc(), "result": {}}
