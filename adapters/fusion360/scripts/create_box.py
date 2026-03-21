"""기본 박스 생성 스크립트 템플릿.

사용법:
    Fusion 360 스크립트 에디터에서 실행하거나,
    script_generator를 통해 파라미터가 주입된 버전을 생성한다.

회귀 테스트 기준: 40×40×5mm 박스 → 부피 = 8000 mm³
"""

import adsk.core
import adsk.fusion
import json
import traceback

# 파라미터 (mm 단위)
WIDTH_MM = 40
HEIGHT_MM = 40
DEPTH_MM = 5


def run(context):
    app = adsk.core.Application.get()
    ui = app.userInterface
    result = {"status": "success", "result": {}}

    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        rootComp = design.rootComponent

        # mm → cm (Fusion 내부 단위)
        w = WIDTH_MM / 10.0
        h = HEIGHT_MM / 10.0
        d = DEPTH_MM / 10.0

        # 스케치 생성
        sketches = rootComp.sketches
        sketch = sketches.add(rootComp.xYConstructionPlane)

        # 사각형 그리기
        lines = sketch.sketchCurves.sketchLines
        lines.addTwoPointRectangle(
            adsk.core.Point3D.create(-w / 2, -h / 2, 0),
            adsk.core.Point3D.create(w / 2, h / 2, 0),
        )

        # 돌출
        prof = sketch.profiles.item(0)
        extrudes = rootComp.features.extrudeFeatures
        distance = adsk.core.ValueInput.createByReal(d)
        ext = extrudes.addSimple(
            prof, distance, adsk.fusion.FeatureOperations.NewBodyFeatureOperation
        )

        # 결과 수집
        body = ext.bodies.item(0)
        props = body.physicalProperties
        bbox = body.boundingBox

        result["result"] = {
            "body_name": body.name,
            "volume_mm3": props.volume * 1000,  # cm³ → mm³
            "surface_area_mm2": props.area * 100,  # cm² → mm²
            "bounding_box": {
                "min": [
                    bbox.minPoint.x * 10,
                    bbox.minPoint.y * 10,
                    bbox.minPoint.z * 10,
                ],
                "max": [
                    bbox.maxPoint.x * 10,
                    bbox.maxPoint.y * 10,
                    bbox.maxPoint.z * 10,
                ],
                "x_size_mm": (bbox.maxPoint.x - bbox.minPoint.x) * 10,
                "y_size_mm": (bbox.maxPoint.y - bbox.minPoint.y) * 10,
                "z_size_mm": (bbox.maxPoint.z - bbox.minPoint.z) * 10,
            },
            "expected_volume_mm3": WIDTH_MM * HEIGHT_MM * DEPTH_MM,
        }
    except Exception:
        result = {"status": "failure", "error": traceback.format_exc(), "result": {}}

    return json.dumps(result)
