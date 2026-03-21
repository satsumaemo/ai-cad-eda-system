"""기본 실린더 생성 스크립트 템플릿."""

import adsk.core
import adsk.fusion
import json
import traceback

DIAMETER_MM = 20
HEIGHT_MM = 30


def run(context):
    app = adsk.core.Application.get()
    result = {"status": "success", "result": {}}

    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        rootComp = design.rootComponent

        r = (DIAMETER_MM / 2) / 10.0  # mm → cm
        h = HEIGHT_MM / 10.0

        sketch = rootComp.sketches.add(rootComp.xYConstructionPlane)
        sketch.sketchCurves.sketchCircles.addByCenterRadius(
            adsk.core.Point3D.create(0, 0, 0), r
        )

        prof = sketch.profiles.item(0)
        extrudes = rootComp.features.extrudeFeatures
        ext = extrudes.addSimple(
            prof,
            adsk.core.ValueInput.createByReal(h),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        )

        body = ext.bodies.item(0)
        props = body.physicalProperties

        import math
        expected_volume = math.pi * (DIAMETER_MM / 2) ** 2 * HEIGHT_MM

        result["result"] = {
            "body_name": body.name,
            "volume_mm3": props.volume * 1000,
            "surface_area_mm2": props.area * 100,
            "expected_volume_mm3": expected_volume,
        }
    except Exception:
        result = {"status": "failure", "error": traceback.format_exc(), "result": {}}

    return json.dumps(result)
