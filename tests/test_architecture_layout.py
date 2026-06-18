from pathlib import Path


def _text_files(root: Path):
    for path in root.rglob("*.py"):
        if "__pycache__" not in path.parts:
            yield path


def test_kit_has_only_package_root_file():
    root = Path(__file__).resolve().parents[1] / "qitos" / "kit"
    top_level_py = sorted(p.name for p in root.glob("*.py"))
    assert top_level_py == ["__init__.py"]


def test_required_kit_packages_exist():
    root = Path(__file__).resolve().parents[1] / "qitos" / "kit"
    expected = {
        "memory",
        "parser",
        "planning",
        "tool",
        "prompts",
        "state",
        "critic",
        "env",
    }
    actual = {p.name for p in root.iterdir() if p.is_dir()}
    assert expected.issubset(actual)


def test_core_does_not_import_implementation_or_product_layers():
    root = Path(__file__).resolve().parents[1]
    forbidden = (
        "qitos.engine",
        "qitos.kit",
        "qitos.benchmark",
        "qitos.recipes",
        "qitos_zoo",
        "examples",
    )
    offenders: list[str] = []
    for path in _text_files(root / "qitos" / "core"):
        text = path.read_text(encoding="utf-8")
        for item in forbidden:
            if f"import {item}" in text or f"from {item}" in text:
                offenders.append(f"{path.relative_to(root)} imports {item}")
    assert offenders == []


def test_architecture_governance_docs_exist():
    root = Path(__file__).resolve().parents[1]
    assert (root / "docs/internal/plans/architecture_cleanliness_audit.md").exists()
    assert (root / "docs/internal/plans/architecture_inventory.md").exists()
