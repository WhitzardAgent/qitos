"""Setup configuration for QitOS."""

from __future__ import annotations

import os
import re
from setuptools import find_packages, setup


def _read_version() -> str:
    here = os.path.abspath(os.path.dirname(__file__))
    init_path = os.path.join(here, "qitos", "__init__.py")
    with open(init_path, encoding="utf-8") as f:
        content = f.read()
    match = re.search(r'^__version__ = [\'"]([^\'"]+)[\'"]', content, re.M)
    if not match:
        raise RuntimeError("Cannot find __version__ in qitos/__init__.py")
    return match.group(1)


def _read_readme() -> str:
    here = os.path.abspath(os.path.dirname(__file__))
    readme_path = os.path.join(here, "README.md")
    with open(readme_path, encoding="utf-8") as f:
        return f.read()


setup(
    name="qitos",
    version=_read_version(),
    description="QitOS - torch-flavor framework for agent researchers",
    long_description=_read_readme(),
    long_description_content_type="text/markdown",
    author="QitOS Team",
    license="MIT",
    url="https://github.com/Qitor/qitos",
    project_urls={
        "Documentation": "https://qitor.mintlify.app/",
        "Quickstart": "https://qitor.mintlify.app/quickstart",
        "Tutorials": "https://qitor.mintlify.app/tutorials",
        "Changelog": "https://github.com/Qitor/qitos/blob/main/CHANGELOG.md",
        "Source": "https://github.com/Qitor/qitos",
        "Issues": "https://github.com/Qitor/qitos/issues",
    },
    keywords=(
        "llm agents, ai agents, agent research, benchmark, reproducibility, "
        "trajectory analysis, coding agent, tool using agents, qita"
    ),
    packages=find_packages(
        exclude=[
            "tests*",
            "examples*",
            "templates*",
            "docs*",
            "plans*",
            "qitos.examples*",
            "qitos_zoo*",
        ]
    ),
    python_requires=">=3.10",
    install_requires=[
        "requests>=2.31.0",
        "beautifulsoup4>=4.12.3",
        "rich>=13.7.0",
        "pyyaml>=6.0",
    ],
    extras_require={
        "models": ["openai>=1.0.0", "litellm>=1.52.0"],
        "yaml": ["pyyaml>=6.0"],
        "benchmarks": ["datasets>=2.20.0", "huggingface_hub>=0.24.0"],
        "wandb": ["wandb>=0.16.0"],
        "mlflow": ["mlflow>=2.0.0"],
        "cookiecutter": ["cookiecutter>=2.1.0"],
        "hf": ["huggingface_hub>=0.24.0"],
        "web": ["playwright>=1.40.0"],
        "workflow": ["qitos-dag>=0.1.0"],
        "dev": [
            "build>=1.2.1",
            "twine>=5.1.1",
            "pytest>=7.0.0",
            "pytest-asyncio>=0.23.0",
            "pytest-cov>=4.0.0",
            "black>=23.0.0",
            "flake8>=6.0.0",
            "mypy>=1.0.0",
            "pre-commit>=3.7.0",
            "pip-audit>=2.7.0",
        ],
        "all": [
            "openai>=1.0.0",
            "litellm>=1.52.0",
            "pyyaml>=6.0",
            "datasets>=2.20.0",
            "huggingface_hub>=0.24.0",
            "wandb>=0.16.0",
            "mlflow>=2.0.0",
            "cookiecutter>=2.1.0",
        ],
    },
    package_data={
        "qitos.benchmark.desktop": ["data/*.json"],
        "qitos.benchmark.tau_bench.port.envs.retail.data": ["*.json", "*.md"],
        "qitos.benchmark.tau_bench.port.envs.airline.data": ["*.json", "*.md"],
        "qitos.benchmark.tau_bench.port.envs.retail": ["*.md"],
        "qitos.benchmark.tau_bench.port.envs.airline": ["*.md"],
    },
    license_files=("LICENSE",),
    include_package_data=True,
    zip_safe=False,
    entry_points={
        "console_scripts": [
            "qit=qitos.cli:main",
            "qita=qitos.qita.cli:main",
        ]
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
)
