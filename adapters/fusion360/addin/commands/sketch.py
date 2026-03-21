"""스케치 생성 커맨드 핸들러.

create_sketch를 대체하는 단순화된 개별 스케치 핸들러:
- handle_create_rectangle_sketch: 사각형 스케치
- handle_create_circle_sketch: 원형 스케치

기존 handle_create_sketch도 하위 호환을 위해 유지한다.
"""

import json
import traceback

try:
    import adsk.core
    import adsk.fusion
except ImportError:
    pass

MM_TO_CM = 0.1


def _get_plane(root, plane_name: str):
    """평면 이름으로 construction plane을 반환한다."""
    plane_map = {
        "xy": root.xYConstructionPlane,
        "xz": root.xZConstructionPlane,
        "yz": root.yZConstructionPlane,
    }
    return plane_map.get(plane_name, root.xYConstructionPlane)


def handle_create_rectangle_sketch(parameters: dict, context: dict) -> dict:
    """평면에 사각형 스케치를 생성한다.

    Args:
        parameters: plane, x_mm, y_mm, width_mm, height_mm
    """
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent

        plane = _get_plane(root, parameters.get("plane", "xy"))
        sketch = root.sketches.add(plane)

        x = parameters.get("x_mm", 0) * MM_TO_CM
        y = parameters.get("y_mm", 0) * MM_TO_CM
        w = parameters["width_mm"] * MM_TO_CM
        h = parameters["height_mm"] * MM_TO_CM

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


def handle_create_circle_sketch(parameters: dict, context: dict) -> dict:
    """평면에 원형 스케치를 생성한다.

    Args:
        parameters: plane, center_x_mm, center_y_mm, radius_mm
    """
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent

        plane = _get_plane(root, parameters.get("plane", "xy"))
        sketch = root.sketches.add(plane)

        cx = parameters.get("center_x_mm", 0) * MM_TO_CM
        cy = parameters.get("center_y_mm", 0) * MM_TO_CM
        r = parameters["radius_mm"] * MM_TO_CM

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


def handle_create_sketch(parameters: dict, context: dict) -> dict:
    """기존 호환용: 복합 스케치 생성.

    Args:
        parameters: plane, face_id, elements
    """
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent

        plane_name = parameters.get("plane", "xy")
        face_id = parameters.get("face_id")
        elements = parameters.get("elements", [])

        if plane_name == "face" and face_id:
            plane = _find_face(root, face_id)
        else:
            plane = _get_plane(root, plane_name)

        sketch = root.sketches.add(plane)

        for elem in elements:
            _add_element(sketch, elem)

        return {
            "status": "success",
            "result": {
                "sketch_name": sketch.name,
                "profile_count": sketch.profiles.count,
                "element_count": len(elements),
            },
        }
    except Exception:
        return {"status": "failure", "error": traceback.format_exc(), "result": {}}


def _add_element(sketch, element: dict) -> None:
    """스케치에 요소를 추가한다."""
    elem_type = element.get("type", "")
    p = element.get("params", {})

    if elem_type == "line":
        p1 = adsk.core.Point3D.create(
            p.get("x1", 0) * MM_TO_CM,
            p.get("y1", 0) * MM_TO_CM,
            0,
        )
        p2 = adsk.core.Point3D.create(
            p.get("x2", 0) * MM_TO_CM,
            p.get("y2", 0) * MM_TO_CM,
            0,
        )
        sketch.sketchCurves.sketchLines.addByTwoPoints(p1, p2)

    elif elem_type == "circle":
        center = adsk.core.Point3D.create(
            p.get("center_x", 0) * MM_TO_CM,
            p.get("center_y", 0) * MM_TO_CM,
            0,
        )
        radius = p.get("radius", 10) * MM_TO_CM
        sketch.sketchCurves.sketchCircles.addByCenterRadius(center, radius)

    elif elem_type == "rectangle":
        p1 = adsk.core.Point3D.create(
            p.get("x", 0) * MM_TO_CM,
            p.get("y", 0) * MM_TO_CM,
            0,
        )
        w = p.get("width", 10) * MM_TO_CM
        h = p.get("height", 10) * MM_TO_CM
        p2 = adsk.core.Point3D.create(
            p.get("x", 0) * MM_TO_CM + w,
            p.get("y", 0) * MM_TO_CM + h,
            0,
        )
        sketch.sketchCurves.sketchLines.addTwoPointRectangle(p1, p2)


def _find_face(comp, face_id: str):
    """face_id로 면을 찾는다."""
    for body in comp.bRepBodies:
        for face in body.faces:
            if str(face.tempId) == face_id:
                return face
    raise ValueError(f"Face not found: {face_id}")
