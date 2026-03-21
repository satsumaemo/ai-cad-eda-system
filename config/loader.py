"""전체 설정 로드 — 환경변수 > YAML > 기본값 우선순위.

모든 모듈이 이 모듈을 통해 설정을 일관되게 로드한다.
"""

import os
from pathlib import Path
from typing import Any

import yaml

# .env 로드 시도 (python-dotenv 미설치 시 무시)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# SSL_CERT_FILE이 미설정이거나 존재하지 않는 경로면 certifi 번들로 교체
_ssl_cert = os.environ.get("SSL_CERT_FILE", "")
if not _ssl_cert or not Path(_ssl_cert).is_file():
    try:
        import certifi
        os.environ["SSL_CERT_FILE"] = certifi.where()
    except ImportError:
        pass

CONFIG_DIR = Path(__file__).parent
PROJECT_ROOT = CONFIG_DIR.parent


def load_config() -> dict[str, Any]:
    """전체 설정을 로드한다.

    우선순위: 환경변수 > YAML 파일 > 하드코딩 기본값

    Returns:
        통합 설정 딕셔너리
    """
    tools = _load_yaml(CONFIG_DIR / "tools.yaml")
    criteria = _load_yaml(CONFIG_DIR / "pass_fail_criteria.yaml")
    units = _load_yaml(CONFIG_DIR / "units.yaml")

    # LLM 프로바이더 설정 (환경변수 > YAML > 기본값)
    llm_yaml = tools.get("llm", {})
    llm_provider = os.getenv("LLM_PROVIDER", llm_yaml.get("provider", "gemini"))
    provider_config = llm_yaml.get(llm_provider, {})
    llm_config = {
        "provider": llm_provider,
        "api_key": (
            os.getenv("GOOGLE_API_KEY") if llm_provider == "gemini"
            else os.getenv("ANTHROPIC_API_KEY")
        ),
        "model": os.getenv(
            "GEMINI_MODEL" if llm_provider == "gemini" else "CLAUDE_MODEL",
            provider_config.get("model", "gemini-2.5-flash" if llm_provider == "gemini" else "claude-sonnet-4-20250514"),
        ),
        "max_tokens": provider_config.get("max_tokens", 8192),
    }

    return {
        # LLM
        "llm": llm_config,
        # 디렉토리 (절대 경로 보장)
        "project_root": str(PROJECT_ROOT),
        "data_dir": os.getenv("DATA_DIR", str(PROJECT_ROOT / "data")),
        "snapshot_dir": os.getenv("SNAPSHOT_DIR", str(PROJECT_ROOT / "data" / "snapshots")),
        "log_dir": os.getenv("LOG_DIR", str(PROJECT_ROOT / "data" / "logs")),
        "work_dir": os.getenv("WORK_DIR", str(PROJECT_ROOT / "data" / "work")),
        # 파이프라인
        "max_iterations": int(os.getenv("MAX_ITERATIONS", "10")),
        "solver_timeout": int(os.getenv("SOLVER_TIMEOUT", "3600")),
        # Fusion 360
        "fusion": {
            "bridge_url": os.getenv("FUSION_BRIDGE_URL", "http://127.0.0.1:18080"),
            "timeout": int(os.getenv("FUSION_BRIDGE_TIMEOUT", "60")),
        },
        # KiCad
        "kicad": {
            "cli_path": os.getenv("KICAD_CLI_PATH", "kicad-cli"),
        },
        # 시뮬레이션 도구
        "simulation": {
            "ccx_path": os.getenv("CCX_PATH", "ccx"),
            "gmsh_path": os.getenv("GMSH_PATH", "gmsh"),
            "elmer_solver_path": os.getenv("ELMER_SOLVER_PATH", "ElmerSolver"),
            "elmer_grid_path": os.getenv("ELMER_GRID_PATH", "ElmerGrid"),
            "paraview_path": os.getenv("PARAVIEW_PATH", "pvpython"),
        },
        # Docker
        "docker": {
            "compose_file": str(PROJECT_ROOT / "env" / "docker" / "docker-compose.yaml"),
            "use_docker": os.getenv("OPENFOAM_DOCKER", "true").lower() == "true",
        },
        # YAML 설정 파일
        "tools": tools,
        "pass_fail_criteria": criteria,
        "units": units,
    }


def _load_yaml(path: Path) -> dict:
    """YAML 파일을 안전하게 로드한다."""
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def get_adapter_config(adapter_name: str) -> dict:
    """특정 어댑터의 설정을 반환한다."""
    config = load_config()
    tools = config.get("tools", {})
    return tools.get("adapters", {}).get(adapter_name, {})
