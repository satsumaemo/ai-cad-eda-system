"""의존성 그래프 테스트."""

import pytest

from pipeline.dependency_graph import DependencyGraph, TaskNode


class TestDependencyGraph:
    def test_simple_linear(self):
        graph = DependencyGraph()
        graph.add_task(TaskNode("mesh", "calculix", "mesh_step"))
        graph.add_task(TaskNode("solve", "calculix", "run_static_analysis", depends_on=["mesh"]))
        graph.add_task(TaskNode("extract", "calculix", "extract_results", depends_on=["solve"]))

        order = graph.get_execution_order()
        assert len(order) == 3
        assert order[0] == ["mesh"]
        assert order[1] == ["solve"]
        assert order[2] == ["extract"]

    def test_parallel_tasks(self):
        graph = DependencyGraph()
        graph.add_task(TaskNode("mesh_a", "calculix", "mesh_step"))
        graph.add_task(TaskNode("mesh_b", "calculix", "mesh_step"))
        graph.add_task(TaskNode("combine", "calculix", "extract_results", depends_on=["mesh_a", "mesh_b"]))

        order = graph.get_execution_order()
        assert len(order) == 2
        assert set(order[0]) == {"mesh_a", "mesh_b"}
        assert order[1] == ["combine"]

    def test_circular_dependency(self):
        graph = DependencyGraph()
        graph.add_task(TaskNode("a", "x", "y", depends_on=["b"]))
        graph.add_task(TaskNode("b", "x", "y", depends_on=["a"]))

        with pytest.raises(ValueError, match="Circular dependency"):
            graph.get_execution_order()

    def test_validate(self):
        graph = DependencyGraph()
        graph.add_task(TaskNode("a", "x", "y"))
        assert graph.validate() is True

    def test_get_dependencies(self):
        graph = DependencyGraph()
        graph.add_task(TaskNode("a", "x", "y"))
        graph.add_task(TaskNode("b", "x", "y", depends_on=["a"]))
        assert graph.get_dependencies("b") == ["a"]
        assert graph.get_dependents("a") == ["b"]
