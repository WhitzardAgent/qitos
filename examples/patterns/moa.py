"""Pattern: Mixture-of-Agents (MoA) — parallel proposals, Aggregator synthesis.

Demonstrates:
- MoAConfig with custom proposers
- build_moa_system() returning aggregator + agent_registry
- Aggregator delegates to proposers and synthesizes proposals
"""

from __future__ import annotations

import os

from qitos import Engine
from qitos.kit.patterns import build_moa_system, MoAConfig
from qitos.models import OpenAICompatibleModel

MODEL_NAME = os.getenv("QITOS_MODEL", "glm-5.1-w4a8")
MODEL_BASE_URL = os.getenv(
    "OPENAI_BASE_URL",
    "https://ekkmopeh8ecgccbjjb9johhhd5dcabcc.openapi-sj.sii.edu.cn/v1/",
)


def build_model() -> OpenAICompatibleModel:
    api_key = (os.getenv("OPENAI_API_KEY") or os.getenv("QITOS_API_KEY") or "").strip()
    if not api_key:
        raise ValueError(
            "Set OPENAI_API_KEY or QITOS_API_KEY before running this example."
        )
    return OpenAICompatibleModel(
        model=MODEL_NAME,
        api_key=api_key,
        base_url=MODEL_BASE_URL,
        temperature=0.4,
        max_tokens=2048,
    )


def main() -> None:
    llm = build_model()

    config = MoAConfig(
        proposers=["security_analyst", "performance_analyst", "reliability_analyst"],
        llm=llm,
        proposer_max_steps=5,
        aggregator_max_steps=10,
    )

    aggregator, agent_registry = build_moa_system(config)

    engine = Engine(
        agent=aggregator,
        agent_registry=agent_registry,
        budget=None,
    )
    result = engine.run(
        "Evaluate the trade-offs of using microservices vs monolith architecture for a fintech platform.",
        max_steps=12,
    )

    print("stop_reason:", result.state.stop_reason)
    print("synthesis:", result.state.synthesis)
    print("proposals_collected:", len(result.state.proposals))


if __name__ == "__main__":
    main()
