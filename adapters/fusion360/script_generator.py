"""tool_use 파라미터 → Fusion Python 스크립트 생성.

각 도구 호출을 Fusion 360 Python API 코드로 변환한다.
생성된 스크립트는 Fusion의 스크립트 실행기 또는 애드인을 통해 실행된다.

단위 규칙:
    - 외부 인터페이스: mm
    - Fusion 360 내부 API: cm (mm / 10)
    - 생성된 스크립트는 내부적으로 mm→cm 변환을 포함
"""

import json
import textwrap
from typing import Any

# Fusion 360 내부 단위는 cm. mm 입력을 cm으로 변환.
MM_TO_CM = 0.1

# 스케치 요소 타입 → 생성 코드 매핑
_ELEMENT_GENERATORS: dict[str, Any] = {}  # 아래에서 등록


def generate_script(action: str, parameters: dict) -> str:
    """액션과 파라미터로부터 Fusion Python 스크립트를 생성한다.

    Args:
        action: 도구 이름 (예: "create_sketch", "extrude")
        parameters: 도구 파라미터

    Returns:
        실행 가능한 Fusion Python 스크립트 문자열

    Raises:
        ValueError: 지원하지 않는 액션
    """
    generator = _SCRIPT_GENERATORS.get(action)
    if generator is None:
        raise ValueError(f"Script generator not found for action: {action}")
    return generator(parameters)


def _gen_create_sketch(params: dict) -> str:
    """create_sketch 스크립트를 생성한다."""
    plane = params.get("plane", "xy")
    elements = params.get("elements", [])
    face_id = params.get("face_id")

    # 평면 선택
    if plane == "face" and face_id:
        plane_code = f'_find_face(rootComp, "{face_id}")'
    else:
        plane_map = {
            "xy": "rootComp.xYConstructionPlane",
            "xz": "rootComp.xZConstructionPlane",
            "yz": "rootComp.yZConstructionPlane",
        }
        plane_code = plane_map.get(plane, "rootComp.xYConstructionPlane")

    # 스케치 요소 코드 생성
    element_lines = []
    for elem in elements:
        code = _generate_element_code(elem)
        if code:
            element_lines.append(code)

    elements_code = "\n    ".join(element_lines) if element_lines else "pass  # No elements"

    return textwrap.dedent(f"""\
        import adsk.core, adsk.fusion, traceback, json

        def run(context):
            app = adsk.core.Application.get()
            ui = app.userInterface
            result = {{"status": "success", "result": {{}}}}
            try:
                design = adsk.fusion.Design.cast(app.activeProduct)
                rootComp = design.rootComponent
                sketches = rootComp.sketches
                sketch = sketches.add({plane_code})

                {elements_code}

                result["result"]["sketch_name"] = sketch.name
                result["result"]["profile_count"] = sketch.profiles.count
            except Exception as e:
                result = {{"status": "failure", "error": str(e), "result": {{}}}}
            return json.dumps(result)
    """)


def _gen_extrude(params: dict) -> str:
    """extrude 스크립트를 생성한다."""
    distance_mm = params.get("distance_mm", 10)
    distance_cm = distance_mm * MM_TO_CM
    profile_index = params.get("profile_index", 0)
    direction = params.get("direction", "positive")
    operation = params.get("operation", "new_body")
    taper = params.get("taper_angle_deg", 0)

    op_map = {
        "new_body": "adsk.fusion.FeatureOperations.NewBodyFeatureOperation",
        "join": "adsk.fusion.FeatureOperations.JoinFeatureOperation",
        "cut": "adsk.fusion.FeatureOperations.CutFeatureOperation",
        "intersect": "adsk.fusion.FeatureOperations.IntersectFeatureOperation",
    }
    op_code = op_map.get(operation, op_map["new_body"])

    direction_code = ""
    if direction == "symmetric":
        direction_code = "extInput.setSymmetricExtent(distance, True)"
    elif direction == "negative":
        direction_code = textwrap.dedent(f"""\
            extInput.setOneSideExtent(
                adsk.fusion.DistanceExtentDefinition.create(distance),
                adsk.fusion.ExtentDirections.NegativeExtentDirection
            )""")

    return textwrap.dedent(f"""\
        import adsk.core, adsk.fusion, traceback, json

        def run(context):
            app = adsk.core.Application.get()
            result = {{"status": "success", "result": {{}}}}
            try:
                design = adsk.fusion.Design.cast(app.activeProduct)
                rootComp = design.rootComponent

                # 가장 최근 스케치의 프로파일 사용
                sketch = rootComp.sketches.item(rootComp.sketches.count - 1)
                prof = sketch.profiles.item({profile_index})

                extrudes = rootComp.features.extrudeFeatures
                distance = adsk.core.ValueInput.createByReal({distance_cm})

                {"# Symmetric or negative direction" if direction != "positive" else ""}
                {f'''extInput = extrudes.createInput(prof, {op_code})
                {direction_code}
                ext = extrudes.add(extInput)''' if direction != "positive" else f'''ext = extrudes.addSimple(prof, distance, {op_code})'''}

                {"" if taper == 0 else f"ext.taperAngle = adsk.core.ValueInput.createByString('{taper} deg')"}

                body = ext.bodies.item(0)
                props = body.physicalProperties
                result["result"]["body_name"] = body.name
                result["result"]["volume_mm3"] = props.volume * 1000  # cm³ → mm³
                result["result"]["surface_area_mm2"] = props.area * 100  # cm² → mm²

                bbox = body.boundingBox
                result["result"]["bounding_box"] = {{
                    "min": [bbox.minPoint.x * 10, bbox.minPoint.y * 10, bbox.minPoint.z * 10],
                    "max": [bbox.maxPoint.x * 10, bbox.maxPoint.y * 10, bbox.maxPoint.z * 10],
                }}
            except Exception as e:
                result = {{"status": "failure", "error": str(e), "result": {{}}}}
            return json.dumps(result)
    """)


def _gen_create_hole(params: dict) -> str:
    """create_hole 스크립트를 생성한다."""
    face_id = params.get("face_id", "")
    cx = params.get("center_x_mm", 0) * MM_TO_CM
    cy = params.get("center_y_mm", 0) * MM_TO_CM
    diameter_cm = params.get("diameter_mm", 5) * MM_TO_CM
    depth_mm = params.get("depth_mm")
    hole_type = params.get("hole_type", "simple")
    through_all = params.get("through_all", False)

    depth_code = ""
    if through_all:
        depth_code = "holeInput.setAllExtent(adsk.fusion.ExtentDirections.NegativeExtentDirection)"
    elif depth_mm:
        depth_cm = depth_mm * MM_TO_CM
        depth_code = f"holeInput.setDistanceExtent(adsk.core.ValueInput.createByReal({depth_cm}))"

    return textwrap.dedent(f"""\
        import adsk.core, adsk.fusion, traceback, json

        def run(context):
            app = adsk.core.Application.get()
            result = {{"status": "success", "result": {{}}}}
            try:
                design = adsk.fusion.Design.cast(app.activeProduct)
                rootComp = design.rootComponent

                face = _find_face(rootComp, "{face_id}")
                point = adsk.core.Point3D.create({cx}, {cy}, 0)

                holes = rootComp.features.holeFeatures
                holeInput = holes.createSimpleInput(
                    adsk.core.ValueInput.createByReal({diameter_cm / 2})
                )
                holeInput.setPositionByPoint(face, point)
                {depth_code}

                hole = holes.add(holeInput)
                result["result"]["hole_name"] = hole.name
                result["result"]["hole_type"] = "{hole_type}"
            except Exception as e:
                result = {{"status": "failure", "error": str(e), "result": {{}}}}
            return json.dumps(result)

        def _find_face(comp, face_id):
            for body in comp.bRepBodies:
                for face in body.faces:
                    if face.tempId == int(face_id) if face_id.isdigit() else False:
                        return face
            # 기본: 첫 번째 바디의 상단면
            body = comp.bRepBodies.item(0)
            top_face = None
            max_z = float('-inf')
            for face in body.faces:
                center = face.centroid
                if center.z > max_z:
                    max_z = center.z
                    top_face = face
            return top_face
    """)


def _gen_rectangular_pattern(params: dict) -> str:
    """rectangular_pattern 스크립트를 생성한다."""
    feature_id = params.get("feature_id", "")
    d1_axis = params.get("direction1_axis", "x")
    d1_count = params.get("direction1_count", 2)
    d1_spacing_cm = params.get("direction1_spacing_mm", 10) * MM_TO_CM
    d2_axis = params.get("direction2_axis")
    d2_count = params.get("direction2_count", 1)
    d2_spacing_cm = params.get("direction2_spacing_mm", 10) * MM_TO_CM if params.get("direction2_spacing_mm") else 0

    axis_map = {
        "x": "rootComp.xConstructionAxis",
        "y": "rootComp.yConstructionAxis",
        "z": "rootComp.zConstructionAxis",
    }
    d1_axis_code = axis_map.get(d1_axis, axis_map["x"])

    d2_code = ""
    if d2_axis and d2_count > 1:
        d2_axis_code = axis_map.get(d2_axis, axis_map["y"])
        d2_code = textwrap.dedent(f"""\
            patInput.setDirectionTwo(
                {d2_axis_code},
                adsk.core.ValueInput.createByReal({d2_count}),
                adsk.core.ValueInput.createByReal({d2_spacing_cm})
            )""")

    return textwrap.dedent(f"""\
        import adsk.core, adsk.fusion, traceback, json

        def run(context):
            app = adsk.core.Application.get()
            result = {{"status": "success", "result": {{}}}}
            try:
                design = adsk.fusion.Design.cast(app.activeProduct)
                rootComp = design.rootComponent

                # 최근 피처 찾기
                timeline = design.timeline
                feature = timeline.item(timeline.count - 1).entity

                patterns = rootComp.features.rectangularPatternFeatures
                entities = adsk.core.ObjectCollection.create()
                entities.add(feature)

                patInput = patterns.createInput(
                    entities,
                    {d1_axis_code},
                    adsk.core.ValueInput.createByReal({d1_count}),
                    adsk.core.ValueInput.createByReal({d1_spacing_cm}),
                    adsk.fusion.PatternDistanceType.SpacingPatternDistanceType
                )
                {d2_code}

                pattern = patterns.add(patInput)
                result["result"]["pattern_name"] = pattern.name
                result["result"]["total_instances"] = {d1_count * d2_count}
            except Exception as e:
                result = {{"status": "failure", "error": str(e), "result": {{}}}}
            return json.dumps(result)
    """)


def _gen_fillet(params: dict) -> str:
    """fillet 스크립트를 생성한다."""
    radius_cm = params.get("radius_mm", 1) * MM_TO_CM
    edge_ids = params.get("edge_ids", [])
    all_edges = params.get("all_edges", False)

    if all_edges:
        edge_selection = textwrap.dedent("""\
            for body in rootComp.bRepBodies:
                for edge in body.edges:
                    edges.add(edge)""")
    else:
        id_list = json.dumps(edge_ids)
        edge_selection = textwrap.dedent(f"""\
            target_ids = {id_list}
            for body in rootComp.bRepBodies:
                for edge in body.edges:
                    if str(edge.tempId) in target_ids:
                        edges.add(edge)""")

    return textwrap.dedent(f"""\
        import adsk.core, adsk.fusion, traceback, json

        def run(context):
            app = adsk.core.Application.get()
            result = {{"status": "success", "result": {{}}}}
            try:
                design = adsk.fusion.Design.cast(app.activeProduct)
                rootComp = design.rootComponent

                fillets = rootComp.features.filletFeatures
                edges = adsk.core.ObjectCollection.create()
                {edge_selection}

                filletInput = fillets.createInput()
                filletInput.addConstantRadiusEdgeSet(
                    edges,
                    adsk.core.ValueInput.createByReal({radius_cm}),
                    True
                )
                fillet = fillets.add(filletInput)
                result["result"]["fillet_name"] = fillet.name
                result["result"]["edge_count"] = edges.count
            except Exception as e:
                result = {{"status": "failure", "error": str(e), "result": {{}}}}
            return json.dumps(result)
    """)


def _gen_chamfer(params: dict) -> str:
    """chamfer 스크립트를 생성한다."""
    distance_cm = params.get("distance_mm", 1) * MM_TO_CM
    edge_ids = params.get("edge_ids", [])
    all_edges = params.get("all_edges", False)

    if all_edges:
        edge_selection = textwrap.dedent("""\
            for body in rootComp.bRepBodies:
                for edge in body.edges:
                    edges.add(edge)""")
    else:
        id_list = json.dumps(edge_ids)
        edge_selection = textwrap.dedent(f"""\
            target_ids = {id_list}
            for body in rootComp.bRepBodies:
                for edge in body.edges:
                    if str(edge.tempId) in target_ids:
                        edges.add(edge)""")

    return textwrap.dedent(f"""\
        import adsk.core, adsk.fusion, traceback, json

        def run(context):
            app = adsk.core.Application.get()
            result = {{"status": "success", "result": {{}}}}
            try:
                design = adsk.fusion.Design.cast(app.activeProduct)
                rootComp = design.rootComponent

                chamfers = rootComp.features.chamferFeatures
                edges = adsk.core.ObjectCollection.create()
                {edge_selection}

                chamferInput = chamfers.createInput2()
                chamferInput.chamferType = adsk.fusion.ChamferType.EqualDistanceChamferType
                chamferInput.addToSelection(edges)
                chamferInput.setToEqualDistance(adsk.core.ValueInput.createByReal({distance_cm}))
                chamfer = chamfers.add(chamferInput)
                result["result"]["chamfer_name"] = chamfer.name
                result["result"]["edge_count"] = edges.count
            except Exception as e:
                result = {{"status": "failure", "error": str(e), "result": {{}}}}
            return json.dumps(result)
    """)


def _gen_set_parameter(params: dict) -> str:
    """set_parameter 스크립트를 생성한다."""
    name = params["name"]
    value = params["value"]
    unit = params.get("unit", "mm")
    comment = params.get("comment", "")

    return textwrap.dedent(f"""\
        import adsk.core, adsk.fusion, traceback, json

        def run(context):
            app = adsk.core.Application.get()
            result = {{"status": "success", "result": {{}}}}
            try:
                design = adsk.fusion.Design.cast(app.activeProduct)
                params = design.userParameters

                existing = params.itemByName("{name}")
                if existing:
                    existing.expression = "{value} {unit}"
                    result["result"]["action"] = "updated"
                else:
                    params.add(
                        "{name}",
                        adsk.core.ValueInput.createByString("{value} {unit}"),
                        "{unit}",
                        "{comment}"
                    )
                    result["result"]["action"] = "created"

                result["result"]["name"] = "{name}"
                result["result"]["value"] = {value}
                result["result"]["unit"] = "{unit}"
            except Exception as e:
                result = {{"status": "failure", "error": str(e), "result": {{}}}}
            return json.dumps(result)
    """)


def _gen_export_step(params: dict) -> str:
    """export_step 스크립트를 생성한다."""
    output_path = params["output_path"].replace("\\", "\\\\")
    component_id = params.get("component_id")

    comp_code = "rootComp"
    if component_id:
        comp_code = f'_find_component(rootComp, "{component_id}")'

    return textwrap.dedent(f"""\
        import adsk.core, adsk.fusion, traceback, json

        def run(context):
            app = adsk.core.Application.get()
            result = {{"status": "success", "result": {{}}}}
            try:
                design = adsk.fusion.Design.cast(app.activeProduct)
                rootComp = design.rootComponent
                component = {comp_code}

                exportMgr = design.exportManager
                stepOptions = exportMgr.createSTEPExportOptions(
                    r"{output_path}", component
                )
                exportMgr.execute(stepOptions)

                result["result"]["output_path"] = r"{output_path}"
                result["result"]["format"] = "STEP AP214"

                # 내보낸 바디의 부피 기록 (검증용)
                total_volume = 0
                for body in component.bRepBodies:
                    total_volume += body.physicalProperties.volume
                result["result"]["volume_mm3"] = total_volume * 1000
            except Exception as e:
                result = {{"status": "failure", "error": str(e), "result": {{}}}}
            return json.dumps(result)

        def _find_component(root, comp_id):
            for occ in root.allOccurrences:
                if occ.component.id == comp_id:
                    return occ.component
            return root
    """)


def _gen_export_stl(params: dict) -> str:
    """export_stl 스크립트를 생성한다."""
    output_path = params["output_path"].replace("\\", "\\\\")
    refinement = params.get("refinement", "medium")

    refinement_map = {
        "low": "adsk.fusion.MeshRefinementSettings.MeshRefinementLow",
        "medium": "adsk.fusion.MeshRefinementSettings.MeshRefinementMedium",
        "high": "adsk.fusion.MeshRefinementSettings.MeshRefinementHigh",
    }
    ref_code = refinement_map.get(refinement, refinement_map["medium"])

    return textwrap.dedent(f"""\
        import adsk.core, adsk.fusion, traceback, json

        def run(context):
            app = adsk.core.Application.get()
            result = {{"status": "success", "result": {{}}}}
            try:
                design = adsk.fusion.Design.cast(app.activeProduct)
                rootComp = design.rootComponent

                exportMgr = design.exportManager
                stlOptions = exportMgr.createSTLExportOptions(rootComp)
                stlOptions.meshRefinement = {ref_code}
                stlOptions.filename = r"{output_path}"
                exportMgr.execute(stlOptions)

                result["result"]["output_path"] = r"{output_path}"
                result["result"]["refinement"] = "{refinement}"
            except Exception as e:
                result = {{"status": "failure", "error": str(e), "result": {{}}}}
            return json.dumps(result)
    """)


def _gen_get_design_info(params: dict) -> str:
    """get_design_info 스크립트를 생성한다."""
    include_params = params.get("include_parameters", True)
    include_bodies = params.get("include_bodies", True)
    include_bbox = params.get("include_bounding_box", True)
    include_mass = params.get("include_mass_properties", False)

    return textwrap.dedent(f"""\
        import adsk.core, adsk.fusion, traceback, json

        def run(context):
            app = adsk.core.Application.get()
            result = {{"status": "success", "result": {{}}}}
            try:
                design = adsk.fusion.Design.cast(app.activeProduct)
                rootComp = design.rootComponent
                info = {{}}

                info["design_name"] = design.rootComponent.name
                info["component_count"] = rootComp.allOccurrences.count + 1

                {"" if not include_params else '''
                user_params = []
                for p in design.userParameters:
                    user_params.append({
                        "name": p.name,
                        "expression": p.expression,
                        "value": p.value,
                        "unit": p.unit,
                        "comment": p.comment,
                    })
                info["parameters"] = user_params
                '''}

                {"" if not include_bodies else '''
                bodies = []
                for body in rootComp.bRepBodies:
                    bodies.append({
                        "name": body.name,
                        "is_visible": body.isVisible,
                        "volume_mm3": body.physicalProperties.volume * 1000,
                        "area_mm2": body.physicalProperties.area * 100,
                    })
                info["bodies"] = bodies
                '''}

                {"" if not include_bbox else '''
                bbox = rootComp.boundingBox
                if bbox:
                    info["bounding_box"] = {
                        "min": [bbox.minPoint.x * 10, bbox.minPoint.y * 10, bbox.minPoint.z * 10],
                        "max": [bbox.maxPoint.x * 10, bbox.maxPoint.y * 10, bbox.maxPoint.z * 10],
                        "x_size_mm": (bbox.maxPoint.x - bbox.minPoint.x) * 10,
                        "y_size_mm": (bbox.maxPoint.y - bbox.minPoint.y) * 10,
                        "z_size_mm": (bbox.maxPoint.z - bbox.minPoint.z) * 10,
                    }
                '''}

                {"" if not include_mass else '''
                mass_props = []
                for body in rootComp.bRepBodies:
                    mp = body.physicalProperties
                    mass_props.append({
                        "body_name": body.name,
                        "volume_mm3": mp.volume * 1000,
                        "area_mm2": mp.area * 100,
                        "center_of_mass": [mp.centerOfMass.x * 10, mp.centerOfMass.y * 10, mp.centerOfMass.z * 10],
                    })
                info["mass_properties"] = mass_props
                '''}

                result["result"] = info
            except Exception as e:
                result = {{"status": "failure", "error": str(e), "result": {{}}}}
            return json.dumps(result)
    """)


def _gen_get_body_properties(params: dict) -> str:
    """get_body_properties 스크립트를 생성한다."""
    body_id = params.get("body_id", "")

    return textwrap.dedent(f"""\
        import adsk.core, adsk.fusion, traceback, json

        def run(context):
            app = adsk.core.Application.get()
            result = {{"status": "success", "result": {{}}}}
            try:
                design = adsk.fusion.Design.cast(app.activeProduct)
                rootComp = design.rootComponent

                body = None
                body_id = "{body_id}"
                for b in rootComp.bRepBodies:
                    if b.name == body_id or str(b.tempId) == body_id:
                        body = b
                        break
                if body is None:
                    body = rootComp.bRepBodies.item(0)

                props = body.physicalProperties
                result["result"] = {{
                    "body_name": body.name,
                    "volume_mm3": props.volume * 1000,
                    "surface_area_mm2": props.area * 100,
                    "center_of_mass_mm": [
                        props.centerOfMass.x * 10,
                        props.centerOfMass.y * 10,
                        props.centerOfMass.z * 10,
                    ],
                    "face_count": body.faces.count,
                    "edge_count": body.edges.count,
                    "vertex_count": body.vertices.count,
                }}

                bbox = body.boundingBox
                result["result"]["bounding_box"] = {{
                    "min": [bbox.minPoint.x * 10, bbox.minPoint.y * 10, bbox.minPoint.z * 10],
                    "max": [bbox.maxPoint.x * 10, bbox.maxPoint.y * 10, bbox.maxPoint.z * 10],
                    "x_size_mm": (bbox.maxPoint.x - bbox.minPoint.x) * 10,
                    "y_size_mm": (bbox.maxPoint.y - bbox.minPoint.y) * 10,
                    "z_size_mm": (bbox.maxPoint.z - bbox.minPoint.z) * 10,
                }}
            except Exception as e:
                result = {{"status": "failure", "error": str(e), "result": {{}}}}
            return json.dumps(result)
    """)


def _gen_create_component(params: dict) -> str:
    """create_component 스크립트를 생성한다."""
    name = params["name"]
    parent_id = params.get("parent_component_id")

    parent_code = "rootComp"
    if parent_id:
        parent_code = f'_find_component(rootComp, "{parent_id}")'

    return textwrap.dedent(f"""\
        import adsk.core, adsk.fusion, traceback, json

        def run(context):
            app = adsk.core.Application.get()
            result = {{"status": "success", "result": {{}}}}
            try:
                design = adsk.fusion.Design.cast(app.activeProduct)
                rootComp = design.rootComponent
                parent = {parent_code}

                occ = parent.occurrences.addNewComponent(adsk.core.Matrix3D.create())
                occ.component.name = "{name}"

                result["result"]["component_name"] = occ.component.name
                result["result"]["component_id"] = occ.component.id
            except Exception as e:
                result = {{"status": "failure", "error": str(e), "result": {{}}}}
            return json.dumps(result)

        def _find_component(root, comp_id):
            for occ in root.allOccurrences:
                if occ.component.id == comp_id:
                    return occ.component
            return root
    """)


def _gen_set_material(params: dict) -> str:
    """set_material 스크립트를 생성한다."""
    body_id = params["body_id"]
    material_name = params["material_name"]

    return textwrap.dedent(f"""\
        import adsk.core, adsk.fusion, traceback, json

        def run(context):
            app = adsk.core.Application.get()
            result = {{"status": "success", "result": {{}}}}
            try:
                design = adsk.fusion.Design.cast(app.activeProduct)
                rootComp = design.rootComponent

                body = None
                for b in rootComp.bRepBodies:
                    if b.name == "{body_id}" or str(b.tempId) == "{body_id}":
                        body = b
                        break
                if body is None:
                    raise ValueError("Body not found: {body_id}")

                # 재질 라이브러리에서 검색
                materialLibs = app.materialLibraries
                material = None
                for lib in materialLibs:
                    for mat in lib.materials:
                        if "{material_name}" in mat.name:
                            material = mat
                            break
                    if material:
                        break

                if material is None:
                    raise ValueError("Material not found: {material_name}")

                body.material = material
                result["result"]["body_name"] = body.name
                result["result"]["material_name"] = material.name
            except Exception as e:
                result = {{"status": "failure", "error": str(e), "result": {{}}}}
            return json.dumps(result)
    """)


def _gen_execute_script(params: dict) -> str:
    """execute_script — 사용자 제공 코드를 래핑한다."""
    code = params["script_code"]
    # 코드 내의 특수문자 이스케이프는 하지 않음 — 코드 그대로 전달
    return code


def _generate_element_code(element: dict) -> str:
    """단일 스케치 요소의 Fusion API 코드를 생성한다."""
    elem_type = element.get("type", "")
    p = element.get("params", {})

    if elem_type == "line":
        x1, y1 = p.get("x1", 0) * MM_TO_CM, p.get("y1", 0) * MM_TO_CM
        x2, y2 = p.get("x2", 0) * MM_TO_CM, p.get("y2", 0) * MM_TO_CM
        return (
            f"sketch.sketchCurves.sketchLines.addByTwoPoints("
            f"adsk.core.Point3D.create({x1}, {y1}, 0), "
            f"adsk.core.Point3D.create({x2}, {y2}, 0))"
        )

    if elem_type == "circle":
        cx, cy = p.get("center_x", 0) * MM_TO_CM, p.get("center_y", 0) * MM_TO_CM
        r = p.get("radius", 10) * MM_TO_CM
        return (
            f"sketch.sketchCurves.sketchCircles.addByCenterRadius("
            f"adsk.core.Point3D.create({cx}, {cy}, 0), {r})"
        )

    if elem_type == "rectangle":
        x, y = p.get("x", 0) * MM_TO_CM, p.get("y", 0) * MM_TO_CM
        w, h = p.get("width", 10) * MM_TO_CM, p.get("height", 10) * MM_TO_CM
        return (
            f"sketch.sketchCurves.sketchLines.addTwoPointRectangle("
            f"adsk.core.Point3D.create({x}, {y}, 0), "
            f"adsk.core.Point3D.create({x + w}, {y + h}, 0))"
        )

    if elem_type == "arc":
        cx, cy = p.get("center_x", 0) * MM_TO_CM, p.get("center_y", 0) * MM_TO_CM
        r = p.get("radius", 10) * MM_TO_CM
        start_angle = p.get("start_angle", 0)
        sweep_angle = p.get("sweep_angle", 90)
        import math
        sx = cx + r * math.cos(math.radians(start_angle))
        sy = cy + r * math.sin(math.radians(start_angle))
        # Three-point arc approximation
        mid_angle = start_angle + sweep_angle / 2
        mx = cx + r * math.cos(math.radians(mid_angle))
        my = cy + r * math.sin(math.radians(mid_angle))
        end_angle = start_angle + sweep_angle
        ex = cx + r * math.cos(math.radians(end_angle))
        ey = cy + r * math.sin(math.radians(end_angle))
        return (
            f"sketch.sketchCurves.sketchArcs.addByThreePoints("
            f"adsk.core.Point3D.create({sx}, {sy}, 0), "
            f"adsk.core.Point3D.create({mx}, {my}, 0), "
            f"adsk.core.Point3D.create({ex}, {ey}, 0))"
        )

    if elem_type == "polygon":
        cx, cy = p.get("center_x", 0) * MM_TO_CM, p.get("center_y", 0) * MM_TO_CM
        r = p.get("radius", 10) * MM_TO_CM
        sides = p.get("sides", 6)
        import math
        lines = []
        for i in range(sides):
            a1 = 2 * math.pi * i / sides
            a2 = 2 * math.pi * (i + 1) / sides
            x1 = cx + r * math.cos(a1)
            y1 = cy + r * math.sin(a1)
            x2 = cx + r * math.cos(a2)
            y2 = cy + r * math.sin(a2)
            lines.append(
                f"sketch.sketchCurves.sketchLines.addByTwoPoints("
                f"adsk.core.Point3D.create({x1}, {y1}, 0), "
                f"adsk.core.Point3D.create({x2}, {y2}, 0))"
            )
        return "\n    ".join(lines)

    return f"# Unsupported element type: {elem_type}"


# ─── 제너레이터 레지스트리 ───

_SCRIPT_GENERATORS: dict[str, Any] = {
    "create_sketch": _gen_create_sketch,
    "extrude": _gen_extrude,
    "create_hole": _gen_create_hole,
    "rectangular_pattern": _gen_rectangular_pattern,
    "fillet": _gen_fillet,
    "chamfer": _gen_chamfer,
    "set_parameter": _gen_set_parameter,
    "export_step": _gen_export_step,
    "export_stl": _gen_export_stl,
    "get_design_info": _gen_get_design_info,
    "get_body_properties": _gen_get_body_properties,
    "create_component": _gen_create_component,
    "set_material": _gen_set_material,
    "execute_script": _gen_execute_script,
}
