"""Fusion Bridge HTTP 수동 테스트 — create_sketch → extrude → set_material.

사용법:
    1. Fusion 360에서 FusionBridge 애드인을 (재)시작한다.
    2. Fusion에서 새 설계(File → New Design)를 연다.
    3. 이 스크립트를 실행한다:
       python tests/test_fusion_bridge_manual.py
"""

import json
import sys
import urllib.request
import urllib.error

BRIDGE = "http://127.0.0.1:18080"


def post(endpoint: str, body: dict) -> dict:
    """POST 요청을 보내고 JSON 응답을 반환한다."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{BRIDGE}{endpoint}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode("utf-8")}
    except urllib.error.URLError as e:
        return {"_connection_error": str(e.reason)}


def main():
    # 0. Health check
    print("=" * 60)
    print("[0] Health check")
    try:
        req = urllib.request.Request(f"{BRIDGE}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            health = json.loads(resp.read().decode("utf-8"))
        print(f"    {json.dumps(health, indent=2)}")
        if not health.get("fusion_running"):
            print("    !! Fusion not running or add-in not loaded. Aborting.")
            sys.exit(1)
    except Exception as e:
        print(f"    !! Cannot reach bridge: {e}")
        sys.exit(1)

    # 1. create_sketch — XY 평면에 40x40mm 사각형
    print()
    print("=" * 60)
    print("[1] create_sketch — XY plane, 40x40mm rectangle")
    result = post("/execute", {
        "action": "create_sketch",
        "parameters": {
            "plane": "xy",
            "elements": [
                {
                    "type": "rectangle",
                    "params": {"x": 0, "y": 0, "width": 40, "height": 40},
                }
            ],
        },
    })
    print(f"    {json.dumps(result, indent=2, ensure_ascii=False)}")
    if result.get("status") != "success":
        print("    !! create_sketch FAILED. Aborting.")
        sys.exit(1)
    print("    OK")

    # 2. extrude — 5mm 돌출
    print()
    print("=" * 60)
    print("[2] extrude — 5mm positive")
    result = post("/execute", {
        "action": "extrude",
        "parameters": {
            "distance_mm": 5,
            "operation": "new_body",
            "direction": "positive",
        },
    })
    print(f"    {json.dumps(result, indent=2, ensure_ascii=False)}")
    if result.get("status") != "success":
        print("    !! extrude FAILED. Aborting.")
        sys.exit(1)
    print("    OK")

    # 3. set_material — Aluminum 6061
    print()
    print("=" * 60)
    print("[3] set_material — Aluminum 6061")
    # extrude 결과에서 body_name 가져오기
    body_name = result.get("result", {}).get("body_name", "Body1")
    result = post("/execute", {
        "action": "set_material",
        "parameters": {
            "body_id": body_name,
            "material_name": "Aluminum 6061",
        },
    })
    print(f"    {json.dumps(result, indent=2, ensure_ascii=False)}")
    if result.get("status") != "success":
        print("    !! set_material FAILED.")
        sys.exit(1)
    print("    OK")

    print()
    print("=" * 60)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
