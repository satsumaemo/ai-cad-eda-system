"""파이프라인 상태 관리 테스트."""

import json
import tempfile
from pathlib import Path

import pytest

from pipeline.state import PipelineState, TaskStatus


class TestPipelineState:
    def test_initial_state(self):
        state = PipelineState()
        assert state.status == TaskStatus.PENDING
        assert state.current_step == 0
        assert len(state.steps) == 0

    def test_record_step(self):
        state = PipelineState()
        state.record_step("fusion360", "create_sketch", {"sketch_id": "s1"})
        assert state.current_step == 1
        assert len(state.steps) == 1
        assert state.steps[0].adapter == "fusion360"

    def test_to_dict(self):
        state = PipelineState()
        state.task_id = "test_task"
        state.record_step("fusion360", "create_sketch", {"sketch_id": "s1"})
        d = state.to_dict()
        assert d["task_id"] == "test_task"
        assert len(d["steps"]) == 1

    def test_checkpoint_save_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "checkpoint.json")

            state = PipelineState()
            state.task_id = "checkpoint_test"
            state.record_step("calculix", "mesh_step", {"mesh_file": "out.msh"})
            state.save_checkpoint(path)

            loaded = PipelineState.load_checkpoint(path)
            assert loaded.task_id == "checkpoint_test"
            assert len(loaded.steps) == 1

    def test_advance_iteration(self):
        state = PipelineState()
        assert state.current_iteration == 0
        state.advance_iteration()
        assert state.current_iteration == 1
