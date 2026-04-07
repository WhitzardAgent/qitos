from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_canonical_examples_exist_and_parse() -> None:
    canonical = [
        "examples/quickstart/minimal_agent.py",
        "examples/patterns/react.py",
        "examples/patterns/planact.py",
        "examples/patterns/reflexion.py",
        "examples/patterns/tot.py",
        "examples/real/coding_agent.py",
        "examples/real/claude_code_agent.py",
        "examples/real/swe_agent.py",
        "examples/real/computer_use_agent.py",
        "examples/real/epub_reader_agent.py",
        "examples/real/code_security_audit_agent.py",
        "examples/real/react_compact_agent.py",
        "examples/real/terminus_2.py",
        "examples/real/whitzard_agent.py",
        "examples/benchmarks/gaia_eval.py",
        "examples/benchmarks/tau_bench_eval.py",
        "examples/benchmarks/cybench_eval.py",
    ]
    for rel in canonical:
        path = ROOT / rel
        assert path.exists(), rel
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_legacy_example_wrappers_are_gone() -> None:
    removed = [
        "examples/coding_agent.py",
        "examples/computer_use_agent.py",
        "examples/epub_reader_tot_agent.py",
        "examples/swe_dynamic_planning_agent.py",
        "examples/real/open_deep_research_gaia_agent.py",
        "examples/real/tau_bench_eval.py",
        "examples/real/cybench_eval.py",
        "examples/common.py",
    ]
    for rel in removed:
        assert not (ROOT / rel).exists(), rel


def test_examples_readme_points_to_canonical_layout() -> None:
    readme = _read("examples/README.md")
    assert "`examples/quickstart/`" in readme
    assert "`examples/patterns/`" in readme
    assert "`examples/real/`" in readme
    assert "`examples/benchmarks/`" in readme
    assert "benchmark/eval runners remain under `examples/real/`" not in readme


def test_benchmark_docs_use_benchmarks_directory() -> None:
    candidate_groups = [
        [
            "docs/builder/benchmark_gaia.md",
            "docs/builder/benchmark_tau.md",
            "docs/zh/builder/benchmark_gaia.md",
            "docs/zh/builder/benchmark_tau.md",
        ],
        [
            "docs/benchmarks/gaia.mdx",
            "docs/benchmarks/tau-bench.mdx",
        ],
    ]
    existing_groups = [
        group for group in candidate_groups if all((ROOT / rel).exists() for rel in group)
    ]
    assert existing_groups, "No benchmark documentation files were found."
    for rel in existing_groups[0]:
        text = _read(rel)
        if "examples/benchmarks/" in text:
            assert "examples/real/open_deep_research_gaia_agent.py" not in text, rel
            assert "examples/real/tau_bench_eval.py" not in text, rel
