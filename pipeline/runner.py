"""파이프라인 실행 엔진 — 단계별 실행, 반복 최적화, 의존성 관리."""

import logging
from typing import Any

from adapters.base import BaseAdapter, Status, ToolResult
from pipeline.dependency_graph import DependencyGraph, TaskNode
from pipeline.error_budget import ErrorBudget
from pipeline.snapshot import SnapshotManager
from pipeline.state import PipelineState, TaskStatus
from validation.gate import ValidationGate

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 10


class PipelineRunner:
    """설계 파이프라인을 실행한다.

    단계별 실행, 검증 게이트 통과, 반복 최적화를 관리한다.
    """

    def __init__(
        self,
        adapters: dict[str, BaseAdapter],
        config: dict | None = None,
    ) -> None:
        self.adapters = adapters
        self.config = config or {}
        self.state = PipelineState()
        self.gate = ValidationGate()
        self.error_budget = ErrorBudget()
        self.snapshot_mgr = SnapshotManager()
        self.max_iterations = self.config.get("max_iterations", DEFAULT_MAX_ITERATIONS)

    def run_step(
        self,
        adapter_name: str,
        action: str,
        parameters: dict,
        criteria: dict | None = None,
    ) -> ToolResult:
        """단일 단계를 실행하고 검증한다."""
        adapter = self.adapters.get(adapter_name)
        if adapter is None:
            raise ValueError(f"Unknown adapter: {adapter_name}")

        self.snapshot_mgr.save(self.state.to_dict())
        self.state.set_status(TaskStatus.RUNNING)

        context = {"state": self.state.to_dict(), "iteration": self.state.current_iteration}
        result = adapter.execute_with_timing(action, parameters, context)
        result = adapter.validate_result(result)

        if criteria:
            gate_result = self.gate.check(result, criteria)
            result.validation.extend(gate_result)

        self.state.record_step(adapter_name, action, result.to_summary())
        return result

    def run_optimization_loop(
        self,
        adapter_name: str,
        action: str,
        initial_params: dict,
        criteria: dict,
        modify_fn: Any = None,
    ) -> ToolResult:
        """반복 최적화 루프를 실행한다.

        Args:
            adapter_name: 어댑터 이름
            action: 실행할 액션
            initial_params: 초기 파라미터
            criteria: pass/fail 기준
            modify_fn: fail 시 파라미터 수정 함수 (result, params) -> params

        Returns:
            최종 ToolResult
        """
        params = dict(initial_params)

        for iteration in range(self.max_iterations):
            self.state.current_iteration = iteration
            logger.info("Optimization iteration %d/%d", iteration + 1, self.max_iterations)

            result = self.run_step(adapter_name, action, params, criteria)

            if result.all_pass:
                logger.info("All checks passed at iteration %d", iteration + 1)
                self.state.set_status(TaskStatus.COMPLETED)
                return result

            if result.has_failure and iteration == self.max_iterations - 1:
                logger.warning("Max iterations reached with failures")
                self.state.set_status(TaskStatus.FAILED)
                return result

            # 파라미터 수정 (modify_fn이 없으면 루프 종료)
            if modify_fn is None:
                logger.info("No modify_fn provided, returning current result")
                return result

            params = modify_fn(result, params)
            self.state.advance_iteration()

        return result

    def run_graph(self, graph: DependencyGraph) -> dict[str, ToolResult]:
        """의존성 그래프에 따라 파이프라인을 실행한다."""
        execution_order = graph.get_execution_order()
        results: dict[str, ToolResult] = {}

        for level in execution_order:
            # 같은 레벨의 작업은 독립적 (향후 병렬화 가능)
            for task_name in level:
                node = graph._nodes[task_name]
                logger.info("Executing: %s (%s.%s)", task_name, node.adapter, node.action)

                result = self.run_step(node.adapter, node.action, node.parameters)
                results[task_name] = result

                if result.has_failure:
                    logger.error("Task %s failed, stopping pipeline", task_name)
                    self.state.set_status(TaskStatus.FAILED)
                    return results

        self.state.set_status(TaskStatus.COMPLETED)
        return results
