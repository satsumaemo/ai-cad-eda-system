"""파라미터 및 재질 커맨드 핸들러."""

import traceback

try:
    import adsk.core
    import adsk.fusion
except ImportError:
    pass


def handle_set_parameter(parameters: dict, context: dict) -> dict:
    """사용자 정의 파라미터를 설정하거나 변경한다."""
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        user_params = design.userParameters

        name = parameters["name"]
        value = parameters["value"]
        unit = parameters.get("unit", "mm")
        comment = parameters.get("comment", "")

        existing = user_params.itemByName(name)
        if existing:
            existing.expression = f"{value} {unit}"
            action = "updated"
        else:
            user_params.add(
                name,
                adsk.core.ValueInput.createByString(f"{value} {unit}"),
                unit,
                comment,
            )
            action = "created"

        return {
            "status": "success",
            "result": {
                "action": action,
                "name": name,
                "value": value,
                "unit": unit,
            },
        }
    except Exception:
        return {"status": "failure", "error": traceback.format_exc(), "result": {}}


# 영어 → 한국어 재질 검색어 매핑 (Fusion 360 한국어판 대응)
_MATERIAL_SEARCH_ALIASES = {
    "aluminum": ["aluminum", "알루미늄"],
    "steel": ["steel", "강", "스틸"],
    "stainless": ["stainless", "스테인리스", "스텐"],
    "copper": ["copper", "구리"],
    "titanium": ["titanium", "티타늄", "티탄"],
    "brass": ["brass", "황동"],
    "nylon": ["nylon", "나일론"],
    "abs": ["abs"],
    "pla": ["pla"],
}


def handle_set_material(parameters: dict, context: dict) -> dict:
    """바디에 재질을 할당한다."""
    try:
        app = adsk.core.Application.get()
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent

        body_id = parameters["body_id"]
        material_name = parameters["material_name"]

        # 바디 찾기 (name 또는 entityToken으로 매칭)
        body = None
        for i in range(root.bRepBodies.count):
            b = root.bRepBodies.item(i)
            if b.name == body_id or b.entityToken == body_id:
                body = b
                break
        # 이름 매칭 실패 시, 마지막 바디 사용 (방금 생성된 바디)
        if body is None and root.bRepBodies.count > 0:
            body = root.bRepBodies.item(root.bRepBodies.count - 1)
        if body is None:
            return {"status": "failure", "error": f"Body not found: {body_id}", "result": {}}

        # 검색 키워드 결정 (영어 → 한국어 별칭 포함)
        search_terms = [material_name.lower()]
        for key, aliases in _MATERIAL_SEARCH_ALIASES.items():
            if key in material_name.lower():
                search_terms.extend(aliases)
                break

        # 재질 찾기 (모든 검색어로 시도)
        material = None
        for lib in app.materialLibraries:
            for i in range(lib.materials.count):
                mat = lib.materials.item(i)
                mat_name_lower = mat.name.lower()
                if any(term in mat_name_lower for term in search_terms):
                    material = mat
                    break
            if material:
                break

        if material is None:
            return {"status": "failure", "error": f"Material not found: {material_name}", "result": {}}

        body.material = material

        return {
            "status": "success",
            "result": {
                "body_name": body.name,
                "material_name": material.name,
            },
        }
    except Exception:
        return {"status": "failure", "error": traceback.format_exc(), "result": {}}
