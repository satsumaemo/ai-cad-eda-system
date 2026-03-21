"""STEP 내보내기 스크립트 템플릿."""

import adsk.core
import adsk.fusion
import json
import traceback

OUTPUT_PATH = "output.step"


def run(context):
    app = adsk.core.Application.get()
    result = {"status": "success", "result": {}}

    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        rootComp = design.rootComponent

        exportMgr = design.exportManager
        stepOptions = exportMgr.createSTEPExportOptions(OUTPUT_PATH, rootComp)
        exportMgr.execute(stepOptions)

        total_volume = sum(
            body.physicalProperties.volume for body in rootComp.bRepBodies
        )

        result["result"] = {
            "output_path": OUTPUT_PATH,
            "format": "STEP AP214",
            "volume_mm3": total_volume * 1000,
            "body_count": rootComp.bRepBodies.count,
        }
    except Exception:
        result = {"status": "failure", "error": traceback.format_exc(), "result": {}}

    return json.dumps(result)
