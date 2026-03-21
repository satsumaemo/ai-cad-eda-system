"""환경 설정 및 config 로더 테스트 (Stage 1 — Fusion 360 전용)."""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent


class TestEnvStructure:
    """env/ 디렉토리 구조 검증."""

    def test_env_example_exists(self):
        assert (PROJECT_ROOT / "env" / ".env.example").exists()

    def test_requirements_dir_exists(self):
        req_dir = PROJECT_ROOT / "env" / "requirements"
        assert req_dir.is_dir()

    def test_scripts_exist(self):
        scripts_dir = PROJECT_ROOT / "env" / "scripts"
        assert (scripts_dir / "check_tools.py").exists()

    def test_gitignore_exists(self):
        assert (PROJECT_ROOT / ".gitignore").exists()

    def test_gitignore_has_essentials(self):
        content = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        essentials = [".env", ".venv/", "__pycache__/", "data/work/"]
        for item in essentials:
            assert item in content, f".gitignore에 {item} 없음"


class TestConfigLoader:
    """config/loader.py 테스트."""

    def test_load_config(self):
        from config.loader import load_config
        config = load_config()
        assert isinstance(config, dict)
        assert "llm" in config
        assert "tools" in config

    def test_load_config_defaults(self):
        from config.loader import load_config
        config = load_config()
        assert config["max_iterations"] == 10
        assert "bridge_url" in config["fusion"]

    def test_fusion360_adapter_enabled(self):
        from config.loader import load_config
        config = load_config()
        tools = config.get("tools", {})
        adapters = tools.get("adapters", {})
        assert "fusion360" in adapters
        assert adapters["fusion360"]["enabled"] is True

    def test_tools_yaml_loaded(self):
        from config.loader import load_config
        config = load_config()
        tools = config.get("tools", {})
        assert "adapters" in tools

    def test_criteria_yaml_loaded(self):
        from config.loader import load_config
        config = load_config()
        criteria = config.get("pass_fail_criteria", {})
        assert len(criteria) > 0
