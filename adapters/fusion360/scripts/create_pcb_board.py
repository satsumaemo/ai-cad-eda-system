"""PCB 기판 형상 생성 스크립트 템플릿.

PCB 기판의 기본 형상을 Fusion에서 생성한다.
열/구조 시뮬레이션용 3D 모델 제작에 사용.

파라미터: 기판 크기, 두께, 모서리 라운딩, 마운팅 홀
"""

import adsk.core
import adsk.fusion
import json
import math
import traceback

# 기본 파라미터 (mm)
BOARD_WIDTH_MM = 100
BOARD_HEIGHT_MM = 80
BOARD_THICKNESS_MM = 1.6
CORNER_RADIUS_MM = 3.0
MOUNTING_HOLE_DIAMETER_MM = 3.2
MOUNTING_HOLE_OFFSET_MM = 5.0  # 모서리로부터 떨어진 거리


def run(context):
    app = adsk.core.Application.get()
    result = {"status": "success", "result": {}}

    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        rootComp = design.rootComponent

        # mm → cm
        w = BOARD_WIDTH_MM / 10.0
        h = BOARD_HEIGHT_MM / 10.0
        t = BOARD_THICKNESS_MM / 10.0
        cr = CORNER_RADIUS_MM / 10.0
        hd = MOUNTING_HOLE_DIAMETER_MM / 10.0
        ho = MOUNTING_HOLE_OFFSET_MM / 10.0

        # 기판 외곽 스케치
        sketch = rootComp.sketches.add(rootComp.xYConstructionPlane)
        lines = sketch.sketchCurves.sketchLines
        arcs = sketch.sketchCurves.sketchArcs

        # 모서리 라운딩 사각형 (수동 구성)
        # 직선 + 호로 구성
        p1 = adsk.core.Point3D.create(cr, 0, 0)
        p2 = adsk.core.Point3D.create(w - cr, 0, 0)
        p3 = adsk.core.Point3D.create(w, cr, 0)
        p4 = adsk.core.Point3D.create(w, h - cr, 0)
        p5 = adsk.core.Point3D.create(w - cr, h, 0)
        p6 = adsk.core.Point3D.create(cr, h, 0)
        p7 = adsk.core.Point3D.create(0, h - cr, 0)
        p8 = adsk.core.Point3D.create(0, cr, 0)

        lines.addByTwoPoints(p1, p2)
        arcs.addByCenterStartSweep(
            adsk.core.Point3D.create(w - cr, cr, 0), p2, math.pi / 2
        )
        lines.addByTwoPoints(p3, p4)
        arcs.addByCenterStartSweep(
            adsk.core.Point3D.create(w - cr, h - cr, 0), p4, math.pi / 2
        )
        lines.addByTwoPoints(p5, p6)
        arcs.addByCenterStartSweep(
            adsk.core.Point3D.create(cr, h - cr, 0), p6, math.pi / 2
        )
        lines.addByTwoPoints(p7, p8)
        arcs.addByCenterStartSweep(
            adsk.core.Point3D.create(cr, cr, 0), p8, math.pi / 2
        )

        # 돌출
        prof = sketch.profiles.item(0)
        extrudes = rootComp.features.extrudeFeatures
        ext = extrudes.addSimple(
            prof,
            adsk.core.ValueInput.createByReal(t),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation,
        )

        # 마운팅 홀 (네 모서리)
        hole_positions = [
            (ho, ho),
            (w - ho, ho),
            (w - ho, h - ho),
            (ho, h - ho),
        ]

        body = ext.bodies.item(0)
        top_face = _find_top_face(body)

        if top_face:
            holes = rootComp.features.holeFeatures
            for hx, hy in hole_positions:
                hole_input = holes.createSimpleInput(
                    adsk.core.ValueInput.createByReal(hd / 2)
                )
                hole_input.setPositionByPoint(
                    top_face, adsk.core.Point3D.create(hx, hy, t)
                )
                hole_input.setAllExtent(
                    adsk.fusion.ExtentDirections.NegativeExtentDirection
                )
                holes.add(hole_input)

        props = body.physicalProperties
        result["result"] = {
            "body_name": body.name,
            "volume_mm3": props.volume * 1000,
            "board_size_mm": f"{BOARD_WIDTH_MM}x{BOARD_HEIGHT_MM}x{BOARD_THICKNESS_MM}",
            "mounting_holes": len(hole_positions),
        }
    except Exception:
        result = {"status": "failure", "error": traceback.format_exc(), "result": {}}

    return json.dumps(result)


def _find_top_face(body):
    """바디의 z 최대면을 찾는다."""
    top_face = None
    max_z = float("-inf")
    for i in range(body.faces.count):
        face = body.faces.item(i)
        z = face.centroid.z
        if z > max_z:
            max_z = z
            top_face = face
    return top_face
