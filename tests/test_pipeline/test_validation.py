"""검증 모듈 테스트."""

import pytest

from adapters.base import Status
from validation.geometry import GeometryValidator
from validation.mesh_quality import MeshQualityChecker


class TestGeometryValidator:
    def test_volume_pass(self):
        validator = GeometryValidator(volume_tol=0.01)
        checks = validator.compare(
            {"volume": 100.0},
            {"volume": 100.5},
        )
        assert len(checks) == 1
        assert checks[0].status == Status.SUCCESS

    def test_volume_fail(self):
        validator = GeometryValidator(volume_tol=0.01)
        checks = validator.compare(
            {"volume": 100.0},
            {"volume": 110.0},
        )
        assert checks[0].status == Status.FAILURE

    def test_triple_check_pass(self):
        validator = GeometryValidator(volume_tol=0.01)
        check = validator.triple_check(100.0, 100.5, 100.3)
        assert check.status == Status.SUCCESS

    def test_triple_check_fail(self):
        validator = GeometryValidator(volume_tol=0.01)
        check = validator.triple_check(100.0, 120.0, 100.0)
        assert check.status == Status.FAILURE

    def test_bbox_comparison(self):
        validator = GeometryValidator(bbox_tol=0.005)
        checks = validator.compare(
            {"bounding_box": [0, 0, 0, 1, 1, 1]},
            {"bounding_box": [0, 0, 0, 1.001, 1.001, 1.001]},
        )
        assert checks[0].status == Status.SUCCESS


class TestMeshQualityChecker:
    def test_all_pass(self):
        checker = MeshQualityChecker()
        checks = checker.check({
            "max_aspect_ratio": 2.0,
            "max_skewness": 0.3,
            "min_jacobian": 0.5,
        })
        assert all(c.status == Status.SUCCESS for c in checks)

    def test_aspect_ratio_fail(self):
        checker = MeshQualityChecker()
        checks = checker.check({"max_aspect_ratio": 15.0})
        assert checks[0].status == Status.FAILURE

    def test_skewness_warning(self):
        checker = MeshQualityChecker()
        checks = checker.check({"max_skewness": 0.6})
        assert checks[0].status == Status.WARNING
