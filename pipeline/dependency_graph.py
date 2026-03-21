"""도구 간 의존성/실행 순서 관리."""

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class TaskNode:
    name: str
    adapter: str
    action: str
    parameters: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)


class DependencyGraph:
    """작업 간 의존성을 관리하고 실행 순서(토폴로지 정렬)를 결정한다."""

    def __init__(self) -> None:
        self._nodes: dict[str, TaskNode] = {}
        self._edges: dict[str, list[str]] = defaultdict(list)  # child -> parents
        self._reverse: dict[str, list[str]] = defaultdict(list)  # parent -> children

    def add_task(self, node: TaskNode) -> None:
        """작업 노드를 추가한다."""
        self._nodes[node.name] = node
        for dep in node.depends_on:
            self._edges[node.name].append(dep)
            self._reverse[dep].append(node.name)

    def get_execution_order(self) -> list[list[str]]:
        """토폴로지 정렬 기반 실행 순서를 반환한다.

        Returns:
            list[list[str]]: 각 내부 리스트는 병렬 실행 가능한 작업 그룹
        """
        in_degree: dict[str, int] = {name: 0 for name in self._nodes}
        for name in self._nodes:
            in_degree[name] = len(self._edges[name])

        queue = deque([name for name, deg in in_degree.items() if deg == 0])
        levels: list[list[str]] = []

        while queue:
            current_level = list(queue)
            queue.clear()
            levels.append(current_level)

            for name in current_level:
                for child in self._reverse[name]:
                    in_degree[child] -= 1
                    if in_degree[child] == 0:
                        queue.append(child)

        scheduled = sum(len(level) for level in levels)
        if scheduled != len(self._nodes):
            unscheduled = set(self._nodes) - {n for level in levels for n in level}
            logger.error("Circular dependency detected: %s", unscheduled)
            raise ValueError(f"Circular dependency: {unscheduled}")

        return levels

    def get_independent_groups(self) -> list[list[str]]:
        """병렬 실행 가능한 독립 작업 그룹을 반환한다."""
        return self.get_execution_order()

    def get_dependencies(self, task_name: str) -> list[str]:
        """특정 작업의 선행 의존성을 반환한다."""
        return list(self._edges.get(task_name, []))

    def get_dependents(self, task_name: str) -> list[str]:
        """특정 작업에 의존하는 후속 작업을 반환한다."""
        return list(self._reverse.get(task_name, []))

    def get_affected_stages(self, modified_stage: str) -> set[str]:
        """특정 단계가 수정되면 영향받는 후속 단계들을 반환한다.

        영향받는 단계만 선택적으로 재실행할 때 사용한다.
        """
        affected: set[str] = set()
        queue = deque([modified_stage])
        while queue:
            current = queue.popleft()
            for child in self._reverse.get(current, []):
                if child not in affected:
                    affected.add(child)
                    queue.append(child)
        return affected

    def add_dependency(self, stage: str, depends_on: str) -> None:
        """노드 없이 의존성만 추가한다 (간편 API)."""
        if stage not in self._nodes:
            self._nodes[stage] = TaskNode(name=stage, adapter="", action="")
        if depends_on not in self._nodes:
            self._nodes[depends_on] = TaskNode(name=depends_on, adapter="", action="")
        self._edges[stage].append(depends_on)
        self._reverse[depends_on].append(stage)

    def validate(self) -> bool:
        """그래프가 유효한지 (순환 없는지) 확인한다."""
        try:
            self.get_execution_order()
            return True
        except ValueError:
            return False
