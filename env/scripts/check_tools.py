"""설치된 도구의 상태를 확인한다. 초기 세팅 후 반드시 실행."""

import os
import subprocess
import sys
from pathlib import Path


def check(name: str, cmd: list[str]) -> bool:
    """커맨드라인 도구의 설치 여부를 확인한다."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        version = (result.stdout.strip() or result.stderr.strip())[:80]
        print(f"  [OK] {name}: {version}")
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        print(f"  [--] {name}: 설치되지 않음 또는 경로 미설정")
        return False


def check_python_package(name: str) -> bool:
    """Python 패키지 설치 여부를 확인한다."""
    try:
        __import__(name)
        print(f"  [OK] {name}: 설치됨")
        return True
    except ImportError:
        print(f"  [--] {name}: 미설치 (pip install {name})")
        return False


def check_docker_service(service: str) -> bool:
    """Docker Compose 서비스 구성 여부를 확인한다."""
    compose_file = Path(__file__).parent.parent / "docker" / "docker-compose.yaml"
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "config", "--services"],
            capture_output=True, text=True, timeout=10,
        )
        if service in result.stdout:
            print(f"  [OK] Docker {service}: 구성됨")
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    print(f"  [--] Docker {service}: 미구성")
    return False


def main() -> None:
    # .env 로드 시도
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    total = 0
    passed = 0

    print("\n=== 필수 도구 ===")
    for name, cmd in [
        ("Python", [sys.executable, "--version"]),
        ("Docker", ["docker", "--version"]),
        ("Docker Compose", ["docker", "compose", "version"]),
    ]:
        total += 1
        if check(name, cmd):
            passed += 1

    print("\n=== Python 패키지 (필수) ===")
    for pkg in ["anthropic", "httpx", "yaml", "jinja2", "pytest"]:
        total += 1
        if check_python_package(pkg):
            passed += 1

    print("\n=== Python 패키지 (선택) ===")
    for pkg in ["dotenv", "pydantic", "numpy", "gmsh"]:
        check_python_package(pkg)  # 선택이므로 카운트 안 함

    print("\n=== CAD 도구 ===")
    fusion_url = os.getenv("FUSION_BRIDGE_URL", "http://127.0.0.1:18080")
    print(f"  [..] Fusion 360: 브릿지 서버 {fusion_url} (수동 확인 필요)")
    kicad_path = os.getenv("KICAD_CLI_PATH", "kicad-cli")
    total += 1
    if check("KiCad CLI", [kicad_path, "--version"]):
        passed += 1

    print("\n=== 시뮬레이션 도구 (Docker) ===")
    for service in ["calculix", "openfoam", "elmer", "openems"]:
        check_docker_service(service)

    print("\n=== 시뮬레이션 도구 (로컬) ===")
    for name, cmd in [
        ("Gmsh", [os.getenv("GMSH_PATH", "gmsh"), "--version"]),
        ("CalculiX", [os.getenv("CCX_PATH", "ccx"), "-v"]),
        ("ElmerSolver", [os.getenv("ELMER_SOLVER_PATH", "ElmerSolver"), "--version"]),
        ("ParaView", [os.getenv("PARAVIEW_PATH", "pvpython"), "--version"]),
    ]:
        check(name, cmd)

    print(f"\n=== 결과: {passed}/{total} 필수 항목 통과 ===\n")


if __name__ == "__main__":
    main()
