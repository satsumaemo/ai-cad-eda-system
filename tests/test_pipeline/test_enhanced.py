"""강화된 pipeline/validation 모듈 테스트."""

import json
import tempfile
from pathlib import Path

import pytest

from adapters.base import Status, ValidationCheck
from pipeline.dependency_graph import DependencyGraph, TaskNode
from pipeline.error_budget import ErrorBudget
from pipeline.snapshot import SnapshotManager
from pipeline.state import PipelineState, TaskStatus
from validation.criteria_loader import CriteriaLoader


class TestDependencyGraphEnhanced:
    """get_affected_stages 및 add_dependency 테스트."""

    def test_affected_stages(self):
        graph = DependencyGraph()
        graph.add_dependency("structural", "modeling")
        graph.add_dependency("thermal", "modeling")
        graph.add_dependency("report", "structural")
        graph.add_dependency("report", "thermal")

        affected = graph.get_affected_stages("modeling")
        assert "structural" in affected
        assert "thermal" in affected
        assert "report" in affected

    def test_affected_stages_partial(self):
        graph = DependencyGraph()
        graph.add_dependency("structural", "modeling")
        graph.add_dependency("pcb_drc", "pcb_layout")

        affected = graph.get_affected_stages("modeling")
        assert "structural" in affected
        assert "pcb_drc" not in affected

    def test_affected_stages_empty(self):
        graph = DependencyGraph()
        graph.add_dependency("b", "a")
        assert graph.get_affected_stages("b") == set()

    def test_add_dependency_creates_nodes(self):
        graph = DependencyGraph()
        graph.add_dependency("child", "parent")
        order = graph.get_execution_order()
        assert len(order) == 2

    def test_full_pipeline_order(self):
        """전체 PCB + 기구 통합 설계 파이프라인 예시."""
        graph = DependencyGraph()
        graph.add_dependency("structural_analysis", "3d_modeling")
        graph.add_dependency("thermal_analysis", "3d_modeling")
        graph.add_dependency("cfd_analysis", "3d_modeling")
        graph.add_dependency("pcb_layout", "schematic")
        graph.add_dependency("em_simulation", "pcb_layout")
        graph.add_dependency("pcb_drc", "pcb_layout")

        order = graph.get_execution_order()
        # Level 0: 3d_modeling, schematic (병렬)
        level0_names = set(order[0])
        assert "3d_modeling" in level0_names
        assert "schematic" in level0_names
        # Level 1: structural, thermal, cfd, pcb_layout (병렬)
        level1_names = set(order[1])
        assert "pcb_layout" in level1_names
        # Level 2: em, drc
        level2_names = set(order[2])
        assert "em_simulation" in level2_names
        assert "pcb_drc" in level2_names


class TestPipelineStateEnhanced:
    def test_can_resume_after_failure(self):
        state = PipelineState()
        state.set_status(TaskStatus.FAILED)
        state.record_step("calc", "run", {"status": "success"})
        assert state.can_resume() is True

    def test_cannot_resume_when_completed(self):
        state = PipelineState()
        state.set_status(TaskStatus.COMPLETED)
        state.record_step("calc", "run", {"status": "success"})
        assert state.can_resume() is False

    def test_get_resume_point(self):
        state = PipelineState()
        state.record_step("step1", "run", {"status": "success"})
        state.record_step("step2", "run", {"status": "success"})
        state.record_step("step3", "run", {"status": "failure"})
        assert state.get_resume_point() == 2  # step2 이후

    def test_get_resume_point_no_success(self):
        state = PipelineState()
        state.record_step("step1", "run", {"status": "failure"})
        assert state.get_resume_point() == 0

    def test_save_stage(self):
        state = PipelineState()
        state.save_stage("thermal", {"adapter": "elmer", "action": "run", "temperature": 85})
        assert state.current_step == 1

    def test_get_history(self):
        state = PipelineState()
        state.record_step("a", "run", {})
        state.record_step("b", "run", {})
        history = state.get_history()
        assert len(history) == 2


class TestErrorBudgetEnhanced:
    def test_suggest_refinement(self):
        budget = ErrorBudget(total_budget=0.05)
        budget.record("mesh", "geometry", 0.02, "mesh 변환 오차")
        budget.record("step_conv", "geometry", 0.03, "STEP 변환 오차")

        suggestion = budget.suggest_refinement()
        assert suggestion is not None
        assert suggestion["step"] == "step_conv"
        assert suggestion["current_error"] == 0.03

    def test_suggest_refinement_empty(self):
        budget = ErrorBudget()
        assert budget.suggest_refinement() is None


class TestSnapshotEnhanced:
    def test_save_and_restore_files(self, tmp_path):
        snap_mgr = SnapshotManager(base_dir=tmp_path / "snaps")

        # 테스트 파일 생성
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "model.step").write_text("STEP data")
        (source_dir / "mesh.inp").write_text("mesh data")

        snap_id = snap_mgr.save_files(
            "proj1", "step1",
            [str(source_dir / "model.step"), str(source_dir / "mesh.inp")],
        )

        # 복원
        restore_dir = tmp_path / "restored"
        restored = snap_mgr.restore_files(snap_id, str(restore_dir))
        assert len(restored) == 2
        assert (restore_dir / "model.step").read_text() == "STEP data"

    def test_list_project_snapshots(self, tmp_path):
        snap_mgr = SnapshotManager(base_dir=tmp_path / "snaps")

        source = tmp_path / "src"
        source.mkdir()
        (source / "a.txt").write_text("data")

        snap_mgr.save_files("proj1", "s1", [str(source / "a.txt")])
        snap_mgr.save_files("proj1", "s2", [str(source / "a.txt")])
        snap_mgr.save_files("proj2", "s1", [str(source / "a.txt")])

        proj1_snaps = snap_mgr.list_project_snapshots("proj1")
        assert len(proj1_snaps) == 2

    def test_restore_nonexistent_raises(self, tmp_path):
        snap_mgr = SnapshotManager(base_dir=tmp_path / "snaps")
        with pytest.raises(FileNotFoundError):
            snap_mgr.restore_files("nonexistent", str(tmp_path))


class TestCriteriaLoader:
    def test_load_default(self):
        loader = CriteriaLoader()
        stages = loader.list_stages()
        assert len(stages) > 0
        assert "geometry" in stages or "structural" in stages

    def test_get_stage_criteria(self):
        loader = CriteriaLoader()
        mesh_criteria = loader.get_stage_criteria("mesh")
        if mesh_criteria:
            assert "aspect_ratio" in mesh_criteria or "skewness" in mesh_criteria

    def test_get_nonexistent_stage(self):
        loader = CriteriaLoader()
        result = loader.get_stage_criteria("nonexistent")
        assert result == {}

    def test_raw_property(self):
        loader = CriteriaLoader()
        raw = loader.raw
        assert isinstance(raw, dict)
