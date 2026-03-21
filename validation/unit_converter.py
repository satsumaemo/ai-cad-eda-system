"""SI ↔ 도구별 단위 변환."""

import logging
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class UnitConverter:
    """내부 SI 단위와 도구별 단위 간 변환을 수행한다."""

    def __init__(self, config_path: str = "config/units.yaml") -> None:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        self.tool_units = config.get("tool_units", {})
        self.conversions = config.get("conversions", {})

    def to_si(self, value: float, unit_type: str, tool_name: str) -> float:
        """도구 단위 → SI 변환."""
        tool_unit = self._get_tool_unit(tool_name, unit_type)
        return self._convert(value, tool_unit, "si", unit_type)

    def from_si(self, value: float, unit_type: str, tool_name: str) -> float:
        """SI → 도구 단위 변환."""
        tool_unit = self._get_tool_unit(tool_name, unit_type)
        return self._convert(value, "si", tool_unit, unit_type)

    def convert_between_tools(
        self, value: float, unit_type: str, from_tool: str, to_tool: str
    ) -> float:
        """도구 A 단위 → 도구 B 단위 변환 (SI를 경유)."""
        si_value = self.to_si(value, unit_type, from_tool)
        return self.from_si(si_value, unit_type, to_tool)

    def _get_tool_unit(self, tool_name: str, unit_type: str) -> str:
        """도구의 해당 단위 타입 단위명을 반환한다."""
        tool = self.tool_units.get(tool_name, {})
        return tool.get(unit_type, "m")  # 기본: SI

    def _convert(self, value: float, from_unit: str, to_unit: str, unit_type: str) -> float:
        """실제 변환을 수행한다."""
        if from_unit == to_unit:
            return value

        # 온도는 오프셋 포함 특수 처리
        if unit_type == "temperature":
            return self._convert_temperature(value, from_unit, to_unit)

        # 길이 변환
        if unit_type == "length":
            return self._convert_length(value, from_unit, to_unit)

        # 주파수 변환
        if unit_type == "frequency":
            return self._convert_frequency(value, from_unit, to_unit)

        # 압력 변환
        if unit_type == "pressure":
            return self._convert_pressure(value, from_unit, to_unit)

        logger.warning("No conversion rule for %s: %s -> %s", unit_type, from_unit, to_unit)
        return value

    def _convert_length(self, value: float, from_unit: str, to_unit: str) -> float:
        """길이 변환 — 모두 m을 기준으로."""
        to_m = {"m": 1.0, "cm": 0.01, "mm": 0.001, "in": 0.0254, "mil": 0.0000254, "si": 1.0}
        from_factor = to_m.get(from_unit, 1.0)
        to_factor = to_m.get(to_unit, 1.0)
        return value * from_factor / to_factor

    def _convert_temperature(self, value: float, from_unit: str, to_unit: str) -> float:
        """온도 변환."""
        # 먼저 켈빈으로
        if from_unit in ("celsius", "si"):
            kelvin = value + 273.15 if from_unit == "celsius" else value
        elif from_unit == "kelvin":
            kelvin = value
        elif from_unit == "fahrenheit":
            kelvin = (value - 32) * 5 / 9 + 273.15
        else:
            kelvin = value

        # 켈빈에서 목표 단위로
        if to_unit == "kelvin" or to_unit == "si":
            return kelvin
        elif to_unit == "celsius":
            return kelvin - 273.15
        elif to_unit == "fahrenheit":
            return (kelvin - 273.15) * 9 / 5 + 32
        return kelvin

    def _convert_frequency(self, value: float, from_unit: str, to_unit: str) -> float:
        """주파수 변환."""
        to_hz = {"Hz": 1.0, "kHz": 1e3, "MHz": 1e6, "GHz": 1e9, "si": 1.0}
        from_factor = to_hz.get(from_unit, 1.0)
        to_factor = to_hz.get(to_unit, 1.0)
        return value * from_factor / to_factor

    def _convert_pressure(self, value: float, from_unit: str, to_unit: str) -> float:
        """압력 변환."""
        to_pa = {"Pa": 1.0, "kPa": 1e3, "MPa": 1e6, "GPa": 1e9, "psi": 6894.76, "bar": 1e5, "si": 1.0}
        from_factor = to_pa.get(from_unit, 1.0)
        to_factor = to_pa.get(to_unit, 1.0)
        return value * from_factor / to_factor
