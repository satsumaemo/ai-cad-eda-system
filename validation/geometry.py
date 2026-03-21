"""부피/표면적/바운딩박스 비교 — 변환 후 형상 무결성 검증."""

import logging
from typing import Any

from adapters.base import Status, ValidationCheck

logger = logging.getLogger(__name__)

# 기본 허용 오차
DEFAULT_VOLUME_TOLERANCE = 0.01       # 1%
DEFAULT_SURFACE_TOLERANCE = 0.01      # 1%
DEFAULT_BBOX_TOLERANCE = 0.005        # 0.5%
WARNING_MULTIPLIER = 3.0              # warning = pass * 3


class GeometryValidator:
    """형상 변환 후 부피, 표면적, 바운딩박스를 비교 검증한다.

    pythonocc 기반 검증을 수행하며, Gmsh/OCC/검증스크립트의
    3중 독립 검증도 지원한다.
    """

    def __init__(
        self,
        volume_tol: float = DEFAULT_VOLUME_TOLERANCE,
        surface_tol: float = DEFAULT_SURFACE_TOLERANCE,
        bbox_tol: float = DEFAULT_BBOX_TOLERANCE,
    ) -> None:
        self.volume_tol = volume_tol
        self.surface_tol = surface_tol
        self.bbox_tol = bbox_tol

    def compare(
        self,
        source: dict[str, Any],
        target: dict[str, Any],
    ) -> list[ValidationCheck]:
        """원본과 변환 후 형상을 비교한다.

        Args:
            source: 원본 형상 정보 {"volume": float, "surface_area": float, "bounding_box": [6 floats]}
            target: 변환 후 형상 정보 (동일 구조)

        Returns:
            ValidationCheck 리스트
        """
        checks: list[ValidationCheck] = []

        # 부피 비교
        if "volume" in source and "volume" in target:
            checks.append(self._compare_scalar(
                "volume", source["volume"], target["volume"], self.volume_tol
            ))

        # 표면적 비교
        if "surface_area" in source and "surface_area" in target:
            checks.append(self._compare_scalar(
                "surface_area", source["surface_area"], target["surface_area"], self.surface_tol
            ))

        # 바운딩박스 비교
        if "bounding_box" in source and "bounding_box" in target:
            checks.append(self._compare_bbox(
                source["bounding_box"], target["bounding_box"]
            ))

        return checks

    def triple_check(
        self,
        fusion_volume: float,
        occ_volume: float,
        script_volume: float,
    ) -> ValidationCheck:
        """3중 독립 검증: Fusion 부피, Gmsh/OCC 부피, 검증 스크립트 부피 비교."""
        values = [fusion_volume, occ_volume, script_volume]
        mean = sum(values) / 3
        max_dev = max(abs(v - mean) / mean for v in values) if mean != 0 else 0

        if max_dev <= self.volume_tol:
            status = Status.SUCCESS
            msg = f"3중 검증 통과: 최대 편차 {max_dev:.4f} <= {self.volume_tol}"
        elif max_dev <= self.volume_tol * WARNING_MULTIPLIER:
            status = Status.WARNING
            msg = f"3중 검증 경고: 최대 편차 {max_dev:.4f}"
        else:
            status = Status.FAILURE
            msg = f"3중 검증 실패: 최대 편차 {max_dev:.4f} > {self.volume_tol * WARNING_MULTIPLIER}"

        return ValidationCheck(
            name="triple_volume_check",
            status=status,
            value={"fusion": fusion_volume, "occ": occ_volume, "script": script_volume, "max_deviation": max_dev},
            threshold=self.volume_tol,
            message=msg,
        )

    def _compare_scalar(
        self, name: str, source_val: float, target_val: float, tolerance: float
    ) -> ValidationCheck:
        """스칼라 값 비교."""
        if source_val == 0:
            deviation = abs(target_val)
        else:
            deviation = abs(target_val - source_val) / abs(source_val)

        if deviation <= tolerance:
            status = Status.SUCCESS
            msg = f"{name}: 편차 {deviation:.4f} <= {tolerance} (PASS)"
        elif deviation <= tolerance * WARNING_MULTIPLIER:
            status = Status.WARNING
            msg = f"{name}: 편차 {deviation:.4f} (WARNING)"
        else:
            status = Status.FAILURE
            msg = f"{name}: 편차 {deviation:.4f} > {tolerance * WARNING_MULTIPLIER} (FAIL)"

        return ValidationCheck(
            name=name,
            status=status,
            value={"source": source_val, "target": target_val, "deviation": deviation},
            threshold=tolerance,
            message=msg,
        )

    def _compare_bbox(
        self, source_bbox: list[float], target_bbox: list[float]
    ) -> ValidationCheck:
        """바운딩박스 비교 (6개 값: xmin, ymin, zmin, xmax, ymax, zmax)."""
        if len(source_bbox) != 6 or len(target_bbox) != 6:
            return ValidationCheck(
                name="bounding_box",
                status=Status.FAILURE,
                value={"source": source_bbox, "target": target_bbox},
                message="바운딩박스 데이터 형식 오류 (6개 값 필요)",
            )

        max_deviation = 0.0
        for s, t in zip(source_bbox, target_bbox):
            ref = max(abs(s), 1e-10)
            dev = abs(t - s) / ref
            max_deviation = max(max_deviation, dev)

        if max_deviation <= self.bbox_tol:
            status = Status.SUCCESS
            msg = f"바운딩박스: 최대 편차 {max_deviation:.4f} <= {self.bbox_tol} (PASS)"
        elif max_deviation <= self.bbox_tol * WARNING_MULTIPLIER:
            status = Status.WARNING
            msg = f"바운딩박스: 최대 편차 {max_deviation:.4f} (WARNING)"
        else:
            status = Status.FAILURE
            msg = f"바운딩박스: 최대 편차 {max_deviation:.4f} (FAIL)"

        return ValidationCheck(
            name="bounding_box",
            status=status,
            value={"max_deviation": max_deviation},
            threshold=self.bbox_tol,
            message=msg,
        )
