"""홀(Hole) 커맨드 핸들러.

Fusion 360의 HoleFeature API 대신 스케치+원+extrude cut 방식을 사용한다.
HoleFeature의 setPositionByPoint가 불안정하기 때문.
"""

import traceback

try:
    import adsk.core
    import adsk.fusion
except ImportError:
    pass

MM_TO_CM = 0.1


def _require_body(root) -> dict | None:
    """bRepBodies가 비어 있으면 에러 dict를 반환, 있으면 None."""
    if root.bRepBodies.count == 0:
        return {
            "status": "failure",
            "error": "바디가 없습니다. 먼저 형상을 생성하세요.",
            "result": {},
        }
    return None


def handle_create_hole(parameters: dict, context: dict) -> dict:
    """지정된 위치에 홀을 생성한다 (스케치+원+extrude cut 방식).

    face_id가 비어있거나 "top"이면 마지막 바디의 Z+ 상단면을 자동으로 찾는다.
    center_x_mm/center_y_mm는 면 중심 기준 오프셋 (0,0 = 면 정중앙).
    """
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent

        err = _require_body(root)
        if err:
            return err

        face_id = parameters.get("face_id", "")
        cx_offset = parameters.get("center_x_mm", 0) * MM_TO_CM
        cy_offset = parameters.get("center_y_mm", 0) * MM_TO_CM
        diameter_mm = parameters.get("diameter_mm", 5)
        radius_cm = (diameter_mm / 2) * MM_TO_CM
        depth_mm = parameters.get("depth_mm")
        through_all = parameters.get("through_all", False)

        face = _find_top_face(root, face_id)
        if face is None:
            return {"status": "failure", "error": "상단면을 찾을 수 없습니다", "result": {}}

        centroid = face.centroid

        # 상단면에 스케치 생성
        sketch = root.sketches.add(face)

        # 면 중심 기준 오프셋 → 스케치 좌표로 변환
        world_point = adsk.core.Point3D.create(
            centroid.x + cx_offset,
            centroid.y + cy_offset,
            centroid.z,
        )
        sketch_point = sketch.modelToSketchSpace(world_point)

        # 원 그리기
        sketch.sketchCurves.sketchCircles.addByCenterRadius(sketch_point, radius_cm)

        # 프로파일 선택 (원 내부 = 가장 작은 면적)
        prof = None
        if sketch.profiles.count == 1:
            prof = sketch.profiles.item(0)
        else:
            min_area = float("inf")
            for i in range(sketch.profiles.count):
                p = sketch.profiles.item(i)
                if p.areaProperties().area < min_area:
                    min_area = p.areaProperties().area
                    prof = p

        if prof is None:
            return {"status": "failure", "error": "홀 프로파일을 찾을 수 없습니다", "result": {}}

        # Extrude cut
        extrudes = root.features.extrudeFeatures
        if through_all or depth_mm is None:
            ext_input = extrudes.createInput(
                prof, adsk.fusion.FeatureOperations.CutFeatureOperation
            )
            ext_input.setAllExtent(adsk.fusion.ExtentDirections.NegativeExtentDirection)
            ext = extrudes.add(ext_input)
        else:
            depth_cm = depth_mm * MM_TO_CM
            ext = extrudes.addSimple(
                prof,
                adsk.core.ValueInput.createByReal(-depth_cm),
                adsk.fusion.FeatureOperations.CutFeatureOperation,
            )

        return {
            "status": "success",
            "result": {
                "hole_name": ext.name,
                "diameter_mm": diameter_mm,
                "depth_mm": depth_mm,
                "through_all": through_all or (depth_mm is None),
                "face_centroid_mm": [
                    round(centroid.x * 10, 2),
                    round(centroid.y * 10, 2),
                    round(centroid.z * 10, 2),
                ],
            },
        }
    except Exception:
        return {"status": "failure", "error": traceback.format_exc(), "result": {}}


def _find_top_face(comp, face_id: str):
    """face_id로 면을 찾거나, 마지막 바디의 Z+ 방향 상단면을 반환한다."""
    if face_id and face_id not in ("", "top", "0"):
        for i in range(comp.bRepBodies.count):
            body = comp.bRepBodies.item(i)
            for j in range(body.faces.count):
                face = body.faces.item(j)
                if face.entityToken == face_id:
                    return face

    if comp.bRepBodies.count == 0:
        return None

    body = comp.bRepBodies.item(comp.bRepBodies.count - 1)
    top_face = None
    max_z = float("-inf")

    for i in range(body.faces.count):
        face = body.faces.item(i)
        evaluator = face.evaluator
        success, normal = evaluator.getNormalAtPoint(face.centroid)
        if success and normal.z > 0.9:
            if face.centroid.z > max_z:
                max_z = face.centroid.z
                top_face = face

    if top_face is None:
        max_z = float("-inf")
        for i in range(body.faces.count):
            face = body.faces.item(i)
            if face.centroid.z > max_z:
                max_z = face.centroid.z
                top_face = face

    return top_face
