"""Pattern: Debate — multi-agent debate with Moderator adjudication.

Demonstrates:
- DebateConfig with custom debaters and rounds
- build_debate_system() returning moderator + agent_registry
- Moderator delegates to debaters and delivers a verdict
"""

from __future__ import annotations

import os

from qitos import Engine
from qitos.kit.patterns import build_debate_system, DebateConfig
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
        temperature=0.3,
        max_tokens=2048,
    )


def main() -> None:
    llm = build_model()

    config = DebateConfig(
        debaters=["proponent", "opponent"],
        rounds=2,
        llm=llm,
        debater_max_steps=3,
        moderator_max_steps=15,
    )

    moderator, agent_registry = build_debate_system(config)

    engine = Engine(
        agent=moderator,
        agent_registry=agent_registry,
        budget=None,
    )
    result = engine.run(
        "Should AI systems be required to explain their decisions?",
        max_steps=15,
    )

    print("stop_reason:", result.state.stop_reason)
    print("verdict:", result.state.verdict)
    print("rounds_completed:", result.state.current_round)
    print("arguments_collected:", len(result.state.arguments))


if __name__ == "__main__":
    main()
