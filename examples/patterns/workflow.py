"""Pattern: Workflow — declarative DAG-based orchestration.

Demonstrates:
- Workflow with add_node() / add_edge() / set_entry()
- Topological execution order with data flowing between nodes
- Error handling with strict_order=True (default)
"""

from __future__ import annotations

from qitos.kit.patterns import Workflow, WorkflowConfig


def search(task: str, context: dict | None = None, **kwargs) -> dict:
    """Search for information on the given task."""
    return {"query": task, "results": ["result_1", "result_2", "result_3"]}


def analyze(task: str, context: dict | None = None, **kwargs) -> dict:
    """Analyze search results."""
    search_data = (context or {}).get("search", {})
    results = search_data.get("results", [])
    return {"analysis": f"Analyzed {len(results)} results", "confidence": 0.85}


def report(task: str, context: dict | None = None, **kwargs) -> dict:
    """Generate a final report."""
    analysis_data = (context or {}).get("analyze", {})
    confidence = analysis_data.get("confidence", 0)
    return {
        "summary": f"Task: {task}",
        "confidence": confidence,
        "recommendation": "proceed" if confidence > 0.7 else "review needed",
    }


def main() -> None:
    wf = Workflow(WorkflowConfig(max_node_retries=1, strict_order=True))
    wf.add_node("search", search, description="Search for relevant information")
    wf.add_node("analyze", analyze, description="Analyze the search results")
    wf.add_node("report", report, description="Generate final report")
    wf.add_edge("search", "analyze")
    wf.add_edge("analyze", "report")
    wf.set_entry("search")

    print("Nodes:", wf.nodes)
    print("Edges:", wf.edges)

    result = wf.run("Investigate the security implications of using REST APIs")

    print("\ncompleted_nodes:", result.completed_nodes)
    print("errors:", result.errors)
    print("node_results:")
    for name, res in result.node_results.items():
        print(f"  {name}: {res}")
    print("stop_reason:", result.stop_reason)


if __name__ == "__main__":
    main()
