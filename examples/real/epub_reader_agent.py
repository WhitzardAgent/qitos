"""Practical EPUB reader: Tree-of-Thought search over chapter evidence."""

# This full application is scheduled to move to qitos-zoo and is not part of QitOS core examples.

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qitos import Action, AgentModule, Decision, StateSchema, ToolRegistry
from qitos.kit import DynamicTreeSearch, EpubToolSet, format_action
from qitos.models import OpenAICompatibleModel

TASK = "Read the EPUB and answer the question with concise evidence."
WORKSPACE = Path("./playground/epub_reader_agent")
EPUB_PATH = WORKSPACE / "book.epub"
QUESTION = "What is the main argument of chapter 1?"
MODEL_NAME = os.getenv("QITOS_MODEL", "Qwen/Qwen3-8B")
MODEL_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.siliconflow.cn/v1/")
MAX_STEPS = 12

THOUGHT_PROMPT = """You are reading an EPUB to answer a question.

Question: {question}
EPUB path: {epub_path}
Known evidence snippets:
{evidence}

Propose 2-4 candidate next thoughts as JSON:
{{
  "thoughts": [
    {{
      "idea": "short thought",
      "score": 0.0_to_1.0,
      "action": {{"name": "epub.list_chapters|epub.search|epub.read_chapter", "args": {{...}}}}
    }}
  ],
  "can_answer": true_or_false,
  "answer": "optional draft answer"
}}
"""


@dataclass
class EpubToTState(StateSchema):
    epub_path: str = EPUB_PATH.name
    question: str = QUESTION
    thoughts: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    chapter_count: int = 0
    scratchpad: list[str] = field(default_factory=list)


class EpubTreeOfThoughtAgent(AgentModule[EpubToTState, dict[str, Any], Action]):
    def __init__(self, llm: Any, workspace_root: str):
        registry = ToolRegistry()
        registry.register_toolset(
            EpubToolSet(workspace_root=workspace_root), namespace="epub"
        )
        super().__init__(tool_registry=registry, llm=llm)

    def init_state(self, task: str, **kwargs: Any) -> EpubToTState:
        return EpubToTState(
            task=task,
            max_steps=int(kwargs.get("max_steps", MAX_STEPS)),
            epub_path=str(kwargs.get("epub_path", EPUB_PATH.name)),
            question=str(kwargs.get("question", QUESTION)),
        )

    def decide(self, state: EpubToTState, observation: dict[str, Any]):
        if state.current_step == 0:
            return Decision.branch(
                candidates=[
                    Decision.act(
                        [
                            Action(
                                name="epub.list_chapters",
                                args={"path": state.epub_path},
                            )
                        ],
                        rationale="enumerate_chapters",
                        meta={"score": 0.95},
                    ),
                    Decision.act(
                        [
                            Action(
                                name="epub.search",
                                args={
                                    "path": state.epub_path,
                                    "query": state.question,
                                    "top_k": 4,
                                },
                            )
                        ],
                        rationale="keyword_probe",
                        meta={"score": 0.8},
                    ),
                ],
                rationale="tot_bootstrap",
            )

        raw = self.llm(
            [
                {"role": "system", "content": "Return valid JSON only."},
                {
                    "role": "user",
                    "content": THOUGHT_PROMPT.format(
                        question=state.question,
                        epub_path=state.epub_path,
                        evidence=self._evidence_block(state),
                    ),
                },
            ]
        )
        parsed = self._parse_json(str(raw))
        if not parsed:
            return self._fallback_decision(state)

        can_answer = bool(parsed.get("can_answer", False))
        answer = str(parsed.get("answer", "")).strip()
        if can_answer and answer and len(state.evidence) >= 2:
            return Decision.final(answer=f"Answer: {answer}")

        candidates: list[Decision[Action]] = []
        for idx, item in enumerate(parsed.get("thoughts", [])):
            if not isinstance(item, dict):
                continue
            action_payload = item.get("action")
            if not isinstance(action_payload, dict):
                continue
            name = str(action_payload.get("name", "")).strip()
            args = action_payload.get("args", {})
            if not name or not isinstance(args, dict):
                continue
            args.setdefault("path", state.epub_path)
            score = item.get("score", 0.5)
            if not isinstance(score, (int, float)):
                score = 0.5
            idea = str(item.get("idea", "")).strip() or f"candidate_{idx}"
            candidates.append(
                Decision.act(
                    [Action(name=name, args=args)],
                    rationale=idea,
                    meta={
                        "score": float(score),
                        "id": f"tot_{state.current_step}_{idx}",
                    },
                )
            )

        if not candidates:
            return self._fallback_decision(state)
        return Decision.branch(candidates=candidates, rationale="tot_branch")

    def reduce(
        self,
        state: EpubToTState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> EpubToTState:
        action_results = (
            observation.get("action_results", [])
            if isinstance(observation, dict)
            else []
        )
        if decision.rationale:
            state.thoughts.append(decision.rationale)
            state.scratchpad.append(f"Thought: {decision.rationale}")
        if decision.actions:
            state.scratchpad.append(f"Action: {format_action(decision.actions[0])}")
        if action_results:
            result = action_results[0]
            state.scratchpad.append(f"Observation: {result}")
            if isinstance(result, dict):
                if isinstance(result.get("chapters"), list):
                    state.chapter_count = len(result.get("chapters") or [])
                    if result.get("chapters"):
                        state.evidence.append(
                            f"chapter_catalog_hint: {result['chapters'][0]}"
                        )
                if isinstance(result.get("hits"), list):
                    for hit in result.get("hits", [])[:3]:
                        if isinstance(hit, dict):
                            state.evidence.append(
                                f"search_hit: {hit.get('snippet', '')}"
                            )
                if isinstance(result.get("content"), str):
                    text = result["content"].strip()
                    if text:
                        state.evidence.append(f"chapter_text: {text[:320]}")
        state.evidence = state.evidence[-20:]
        state.thoughts = state.thoughts[-40:]
        state.scratchpad = state.scratchpad[-40:]
        return state

    def _fallback_decision(self, state: EpubToTState) -> Decision[Action]:
        if state.chapter_count <= 0:
            return Decision.act(
                [Action(name="epub.list_chapters", args={"path": state.epub_path})],
                rationale="fallback_list_chapters",
                meta={"score": 0.6},
            )
        next_idx = min(
            max(0, len(state.evidence) // 2), max(0, state.chapter_count - 1)
        )
        return Decision.act(
            [
                Action(
                    name="epub.read_chapter",
                    args={
                        "path": state.epub_path,
                        "chapter_index": int(next_idx),
                        "max_chars": 5000,
                    },
                )
            ],
            rationale="fallback_read_next",
            meta={"score": 0.55},
        )

    def _evidence_block(self, state: EpubToTState) -> str:
        if not state.evidence:
            return "- none"
        return "\n".join(f"- {item}" for item in state.evidence[-8:])

    def _parse_json(self, raw: str) -> dict[str, Any] | None:
        text = raw.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            pass
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                return None
        return None


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
        temperature=0.2,
        max_tokens=2048,
    )


def main() -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    if not EPUB_PATH.exists():
        raise FileNotFoundError(
            f"Expected an EPUB at {EPUB_PATH}. Place a sample book there before running this example."
        )

    agent = EpubTreeOfThoughtAgent(llm=build_model(), workspace_root=str(WORKSPACE))
    result = agent.run(
        task=TASK,
        workspace=str(WORKSPACE),
        epub_path=EPUB_PATH.name,
        question=QUESTION,
        max_steps=MAX_STEPS,
        search=DynamicTreeSearch(top_k=2),
        return_state=True,
    )

    print("workspace:", WORKSPACE)
    print("epub_path:", EPUB_PATH)
    print("final_result:", result.state.final_result)
    print("stop_reason:", result.state.stop_reason)
    print("evidence_count:", len(result.state.evidence))


if __name__ == "__main__":
    main()
