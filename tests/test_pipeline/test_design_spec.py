"""DesignSpec 단위 테스트.

설계 명세 데이터 구조의 생성, 직렬화, 파라미터 관리,
변경 이력 자동 기록, LLM 컨텍스트 변환을 검증한다.
"""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.design_spec import (
    DesignConstraint,
    DesignDelta,
    DesignObjective,
    DesignParameter,
    DesignSpec,
)


class TestDesignParameter:
    def test_create(self):
        p = DesignParameter(name="width", value=40.0, unit="mm")
        assert p.name == "width"
        assert p.value == 40.0
        assert p.unit == "mm"

    def test_to_dict_excludes_empty(self):
        p = DesignParameter(name="width", value=40.0)
        d = p.to_dict()
        assert "name" in d
        assert "value" in d
        assert "unit" not in d  # empty string excluded


class TestDesignSpec:
    def test_create_empty(self):
        spec = DesignSpec(name="test_bracket")
        assert spec.name == "test_bracket"
        assert len(spec.parameters) == 0
        assert len(spec.constraints) == 0
        assert len(spec.objectives) == 0
        assert len(spec.history) == 0

    def test_set_parameter_records_delta(self):
        spec = DesignSpec()
        spec.set_parameter("width", 40.0, unit="mm", source="user")

        assert "width" in spec.parameters
        assert spec.parameters["width"].value == 40.0
        assert len(spec.history) == 1
        assert spec.history[0].parameter_name == "width"
        assert spec.history[0].old_value is None
        assert spec.history[0].new_value == 40.0
        assert spec.history[0].source == "user"

    def test_update_parameter_records_delta(self):
        spec = DesignSpec()
        spec.set_parameter("width", 40.0, source="user")
        spec.set_parameter("width", 50.0, source="optimizer")

        assert spec.parameters["width"].value == 50.0
        assert len(spec.history) == 2
        assert spec.history[1].old_value == 40.0
        assert spec.history[1].new_value == 50.0

    def test_same_value_no_delta(self):
        spec = DesignSpec()
        spec.set_parameter("width", 40.0)
        spec.set_parameter("width", 40.0)
        assert len(spec.history) == 1  # no duplicate

    def test_get_parameter(self):
        spec = DesignSpec()
        spec.set_parameter("height", 20.0, unit="mm")
        p = spec.get_parameter("height")
        assert p is not None
        assert p.value == 20.0

    def test_get_parameter_missing(self):
        spec = DesignSpec()
        assert spec.get_parameter("nonexistent") is None

    def test_remove_parameter(self):
        spec = DesignSpec()
        spec.set_parameter("width", 40.0)
        assert spec.remove_parameter("width") is True
        assert spec.get_parameter("width") is None
        assert spec.remove_parameter("width") is False

    def test_add_constraint(self):
        spec = DesignSpec()
        spec.add_constraint("min_width", "width >= 10", description="최소 폭")
        assert len(spec.constraints) == 1
        assert spec.constraints[0].name == "min_width"
        assert spec.constraints[0].expression == "width >= 10"

    def test_add_objective(self):
        spec = DesignSpec()
        spec.add_objective("min_mass", "minimize", "mass", weight=2.0)
        assert len(spec.objectives) == 1
        assert spec.objectives[0].target == "minimize"
        assert spec.objectives[0].weight == 2.0

    def test_to_dict_roundtrip(self):
        spec = DesignSpec(name="bracket", description="Test bracket")
        spec.set_parameter("width", 40.0, unit="mm", min_value=10.0, max_value=100.0)
        spec.set_parameter("height", 20.0, unit="mm")
        spec.add_constraint("min_width", "width >= 10")
        spec.add_objective("min_mass", "minimize", "mass")
        spec.metadata["version"] = "1.0"

        data = spec.to_dict()
        restored = DesignSpec.from_dict(data)

        assert restored.name == "bracket"
        assert restored.description == "Test bracket"
        assert len(restored.parameters) == 2
        assert restored.parameters["width"].value == 40.0
        assert restored.parameters["width"].min_value == 10.0
        assert len(restored.constraints) == 1
        assert len(restored.objectives) == 1
        assert len(restored.history) == 2
        assert restored.metadata["version"] == "1.0"

    def test_json_roundtrip(self):
        spec = DesignSpec(name="test")
        spec.set_parameter("x", 10.0, unit="mm")
        json_str = spec.to_json()

        restored = DesignSpec.from_json(json_str)
        assert restored.name == "test"
        assert restored.parameters["x"].value == 10.0

    def test_save_and_load(self, tmp_path: Path):
        spec = DesignSpec(name="saved_spec")
        spec.set_parameter("depth", 5.0, unit="mm")
        spec.add_constraint("max_depth", "depth <= 50")

        file_path = tmp_path / "spec.json"
        spec.save(file_path)

        loaded = DesignSpec.load(file_path)
        assert loaded.name == "saved_spec"
        assert loaded.parameters["depth"].value == 5.0
        assert len(loaded.constraints) == 1

    def test_to_llm_context(self):
        spec = DesignSpec(name="bracket", description="L-bracket for mounting")
        spec.set_parameter("width", 40.0, unit="mm", min_value=10.0, max_value=100.0)
        spec.set_parameter("height", 20.0, unit="mm")
        spec.add_constraint("min_width", "width >= 10")
        spec.add_objective("min_mass", "minimize", "mass")

        ctx = spec.to_llm_context()

        assert "bracket" in ctx
        assert "L-bracket" in ctx
        assert "width: 40.0 mm" in ctx
        assert "[10.0, 100.0]" in ctx
        assert "height: 20.0 mm" in ctx
        assert "width >= 10" in ctx
        assert "minimize mass" in ctx

    def test_get_recent_changes(self):
        spec = DesignSpec()
        spec.set_parameter("a", 1.0)
        spec.set_parameter("b", 2.0)
        spec.set_parameter("c", 3.0)
        spec.set_parameter("a", 10.0)

        # 타임스탬프가 동일할 수 있으므로 수동으로 설정
        for i, delta in enumerate(spec.history):
            delta.timestamp = float(i)

        recent = spec.get_recent_changes(2)
        assert len(recent) == 2
        # 최신 먼저 (timestamp가 큰 순서)
        assert recent[0].new_value == 10.0

    def test_repr(self):
        spec = DesignSpec(name="test")
        spec.set_parameter("x", 1.0)
        r = repr(spec)
        assert "test" in r
        assert "params=1" in r

    def test_parameter_with_range(self):
        spec = DesignSpec()
        spec.set_parameter(
            "thickness", 5.0,
            unit="mm",
            min_value=1.0,
            max_value=20.0,
            description="Wall thickness",
        )
        p = spec.get_parameter("thickness")
        assert p.min_value == 1.0
        assert p.max_value == 20.0
        assert p.description == "Wall thickness"

    def test_empty_spec_to_llm_context(self):
        spec = DesignSpec()
        ctx = spec.to_llm_context()
        assert "Design Specification" in ctx


class TestDesignSpecLLMIndependence:
    """DesignSpec이 LLM 프로바이더에 독립적인지 확인한다.

    표준화된 중간 계층으로서 동일한 DesignSpec 데이터가
    어떤 LLM 클라이언트로도 동일하게 전달될 수 있어야 한다.
    """

    def _make_spec(self) -> DesignSpec:
        spec = DesignSpec(name="bracket_v1", description="L-bracket")
        spec.set_parameter("width", 40.0, unit="mm", min_value=10.0, max_value=100.0)
        spec.set_parameter("height", 20.0, unit="mm")
        spec.set_parameter("thickness", 3.0, unit="mm")
        spec.add_constraint("min_width", "width >= 10")
        spec.add_objective("min_mass", "minimize", "mass")
        return spec

    def test_spec_dict_is_json_serializable(self):
        """DesignSpec.to_dict()은 JSON 직렬화 가능해야 한다."""
        spec = self._make_spec()
        json_str = json.dumps(spec.to_dict(), ensure_ascii=False)
        assert isinstance(json_str, str)
        assert len(json_str) > 0

    def test_spec_llm_context_is_plain_text(self):
        """to_llm_context()는 순수 텍스트여야 한다 (프로바이더 특정 형식 아님)."""
        spec = self._make_spec()
        ctx = spec.to_llm_context()
        assert isinstance(ctx, str)
        # LLM 프로바이더 특정 형식이 포함되지 않아야 함
        assert "anthropic" not in ctx.lower()
        assert "gemini" not in ctx.lower()
        assert "openai" not in ctx.lower()

    def test_spec_roundtrip_preserves_all_data(self):
        """직렬화-역직렬화 후 모든 데이터가 보존되어야 한다."""
        spec = self._make_spec()
        restored = DesignSpec.from_dict(spec.to_dict())

        assert restored.name == spec.name
        assert len(restored.parameters) == len(spec.parameters)
        assert len(restored.constraints) == len(spec.constraints)
        assert len(restored.objectives) == len(spec.objectives)
        for name in spec.parameters:
            assert restored.parameters[name].value == spec.parameters[name].value
            assert restored.parameters[name].unit == spec.parameters[name].unit
