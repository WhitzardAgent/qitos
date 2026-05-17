from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


CANONICAL = [
    "examples/quickstart/minimal_agent.py",
    "examples/patterns/react.py",
    "examples/patterns/planact.py",
    "examples/patterns/reflexion.py",
    "examples/patterns/tot.py",
    "examples/real/research_harness_agent.py",
    "examples/real/coding_agent.py",
    "examples/real/desktop_env_smoke.py",
    "examples/benchmarks/gaia_eval.py",
    "examples/benchmarks/tau_bench_eval.py",
    "examples/benchmarks/cybench_eval.py",
]

ZOO_CANDIDATES = [
    "examples/real/claude_code_agent.py",
    "examples/real/code_security_audit_agent.py",
    "examples/real/swe_agent.py",
    "examples/real/computer_use_agent.py",
    "examples/real/openai_cua_agent.py",
    "examples/real/epub_reader_agent.py",
    "examples/real/whitzard_agent.py",
    "examples/real/skillhub_github_agent.py",
]


def test_canonical_examples_exist_and_parse() -> None:
    for rel in CANONICAL:
        path = ROOT / rel
        assert path.exists(), rel
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_examples_readme_does_not_recommend_zoo_candidates_first() -> None:
    readme = (ROOT / "examples/README.md").read_text(encoding="utf-8")
    first_run = readme.split("## Benchmark", 1)[0]
    for rel in ZOO_CANDIDATES:
        assert rel not in first_run
    assert "qitos-zoo" in readme
    assert "qitos-coder" in readme
    assert "qitos-cyber-agent" in readme


def test_zoo_candidates_have_migration_banner() -> None:
    banner = (
        "This full application is scheduled to move to qitos-zoo and is not part "
        "of QitOS core examples."
    )
    for rel in ZOO_CANDIDATES:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert banner in text[:800], rel


def test_migration_staging_manifest_exists() -> None:
    root = ROOT / "plans" / "qitos_zoo_migration"
    assert (root / "README.md").exists()
    assert (root / "MIGRATION_MANIFEST.md").exists()
    assert (root / "apps" / "qitos-coder").exists()
    assert (root / "apps" / "qitos-cyber-agent").exists()


def test_packaging_excludes_qitos_examples_and_plans() -> None:
    from setuptools import find_packages

    packages = set(
        find_packages(
            where=str(ROOT),
            exclude=[
                "tests*",
                "examples*",
                "templates*",
                "docs*",
                "plans*",
                "qitos.examples*",
            ],
        )
    )
    assert "qitos.examples" not in packages
    assert not any(item.startswith("qitos.examples.") for item in packages)
    assert not any(item.startswith("plans") for item in packages)
