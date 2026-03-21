"""config/pass_fail_criteria.yaml 로드 및 단계별 기준 조회."""

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CRITERIA_PATH = "config/pass_fail_criteria.yaml"


class CriteriaLoader:
    """판정 기준을 YAML에서 로드하고 단계별로 조회한다."""

    def __init__(self, config_path: str = DEFAULT_CRITERIA_PATH) -> None:
        self._path = config_path
        self._criteria: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, encoding="utf-8") as f:
                self._criteria = yaml.safe_load(f) or {}
            logger.info("Loaded criteria from %s", self._path)
        except FileNotFoundError:
            logger.warning("Criteria file not found: %s", self._path)
            self._criteria = {}

    def get_stage_criteria(self, stage: str) -> dict:
        """특정 단계의 판정 기준을 반환한다."""
        return self._criteria.get(stage, {})

    def get_metric_criterion(self, stage: str, metric: str) -> dict | None:
        """특정 단계의 특정 메트릭 기준을 반환한다."""
        return self._criteria.get(stage, {}).get(metric)

    def list_stages(self) -> list[str]:
        """정의된 단계 목록을 반환한다."""
        return list(self._criteria.keys())

    def reload(self) -> None:
        """기준을 다시 로드한다."""
        self._load()

    @property
    def raw(self) -> dict:
        """원본 기준 딕셔너리를 반환한다."""
        return dict(self._criteria)
