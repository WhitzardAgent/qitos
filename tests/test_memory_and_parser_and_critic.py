from qitos.core.memory import MemoryRecord
from qitos.kit.critic import PassThroughCritic
from qitos.kit.memory import SummaryMemory, VectorMemory, WindowMemory
from qitos.kit.parser import (
    JsonDecisionParser,
    ReActTextParser,
    XmlDecisionParser,
    parse_first_action_invocation,
    split_args_robust,
)
from qitos.kit.parser.parser_utils import extract_balanced_object_candidates


def test_memory_adapters_basic():
    win = WindowMemory(window_size=2)
    win.append(MemoryRecord(role="user", content="a", step_id=0))
    win.append(MemoryRecord(role="assistant", content="b", step_id=1))
    win.append(MemoryRecord(role="user", content="c", step_id=2))
    assert [r.content for r in win.retrieve()] == ["b", "c"]

    summary = SummaryMemory(keep_last=2)
    summary.append(MemoryRecord(role="user", content="alpha", step_id=0))
    summary.append(MemoryRecord(role="assistant", content="beta", step_id=1))
    assert "alpha" in summary.summarize(max_items=2)

    vec = VectorMemory(top_k=1)
    vec.append(MemoryRecord(role="user", content="python docs", step_id=0))
    vec.append(MemoryRecord(role="user", content="flight booking", step_id=1))
    top = vec.retrieve({"text": "python", "top_k": 1})
    assert len(top) == 1


def test_parser_and_critic_impls():
    d1 = JsonDecisionParser().parse('{"mode":"wait"}')
    assert d1.mode == "wait"

    d2 = ReActTextParser().parse("Thought: done now\nFinal Answer: done")
    assert d2.mode == "final"
    assert d2.rationale == "done now"
    d2b = ReActTextParser().parse(
        "Thought: x\nAction: {'name': 'add', 'args': {'a': 2, 'b': 3}}"
    )
    assert d2b.mode == "act"
    assert d2b.actions[0]["name"] == "add"
    assert d2b.rationale == "x"

    long_html = "<html><head><script>var x = {a:1,b:2};</script></head><body>Hello, world</body></html>"
    raw = f"Thought: parse\nAction: extract_web_text(html={long_html!r}, max_chars=6000, keep_links=False)"
    d2c = ReActTextParser().parse(raw)
    assert d2c.mode == "act"
    assert d2c.actions[0]["name"] == "extract_web_text"
    assert d2c.actions[0]["args"]["max_chars"] == 6000
    assert d2c.actions[0]["args"]["keep_links"] is False
    assert "Hello, world" in d2c.actions[0]["args"]["html"]
    assert d2c.rationale == "parse"

    d3 = XmlDecisionParser().parse('<decision mode="wait"></decision>')
    assert d3.mode == "wait"

    critic = PassThroughCritic()
    out = critic.evaluate(state={}, decision=d1, results=[])
    assert out["action"] == "continue"


def test_built_in_parsers_return_structured_diagnostics_for_recoverable_failures():
    react = ReActTextParser().parse("Thought: I should inspect more closely.")
    assert react.mode == "wait"
    assert react.meta["parser_error"] is True
    assert react.meta["parser_diagnostics"]["code"] == "missing_action_or_final"
    assert "Action:" in react.meta["parser_feedback"]

    js = JsonDecisionParser().parse("not valid json")
    assert js.mode == "wait"
    assert js.meta["parser_error"] is True
    assert js.meta["parser_diagnostics"]["code"] == "invalid_json"
    assert "Return valid JSON" in js.meta["parser_feedback"]

    xml = XmlDecisionParser().parse("<decision><think>x</think>")
    assert xml.mode == "wait"
    assert xml.meta["parser_error"] is True
    assert xml.meta["parser_diagnostics"]["code"] in {
        "invalid_xml",
        "missing_action_or_final",
    }
    assert (
        "Return XML" in xml.meta["parser_feedback"]
        or "Return well-formed XML" in xml.meta["parser_feedback"]
    )


def test_json_decision_parser_salvages_wrapped_json_like_blocks():
    raw = """Here is the decision block:

```json
{'thought': 'use search', 'action': {'name': 'web_search', 'args': {'query': 'vim modeline security'}}}
```

No additional notes.
"""
    decision = JsonDecisionParser().parse(raw)
    assert decision.mode == "act"
    assert decision.actions[0]["name"] == "web_search"
    assert decision.meta["parser_diagnostics"]["extraction_mode"] == "python_literal"
    assert decision.meta["parser_diagnostics"]["salvage_applied"] is True


def test_balanced_object_extraction_ignores_preface_apostrophes():
    expected_json = (
        '{"thought":"Run nmap scan.",'
        '"action":{"name":"nmap_scan","args":{"target":"10.0.0.1"}}}'
    )
    raw = """I'll start by scanning the target.

{"thought":"Run nmap scan.","action":{"name":"nmap_scan","args":{"target":"10.0.0.1"}}}"""
    candidates = extract_balanced_object_candidates(raw)
    assert candidates == [expected_json]

    expected_literal = (
        "{'thought': 'look at {brace', "
        "'action': {'name': 'web_search', "
        "'args': {'query': 'vim modeline security'}}}"
    )
    literal_raw = """Here is the decision block:

{'thought': 'look at {brace', 'action': {'name': 'web_search', 'args': {'query': 'vim modeline security'}}}

No additional notes.
"""
    literal_candidates = extract_balanced_object_candidates(literal_raw)
    assert literal_candidates == [expected_literal]


def test_func_parser_handles_nested_and_truncated_calls():
    s = "a=1, payload={'x':[1,2,3], 'y':'k,v'}, html='<div>(x)</div>', flag=true"
    parts = split_args_robust(s)
    assert len(parts) == 4

    parsed = parse_first_action_invocation(
        "Thought: x\nAction: tool(a=1, b='x,y', c={'k':[1,2]})"
    )
    assert parsed is not None
    assert parsed["name"] == "tool"
    assert parsed["args"]["a"] == 1
    assert parsed["args"]["b"] == "x,y"
    assert parsed["args"]["c"]["k"] == [1, 2]

    # truncated tail: still recover partial kwargs
    parsed2 = parse_first_action_invocation(
        "Action: extract_web_text(html='<html><body>abc', max_chars=5000"
    )
    assert parsed2 is not None
    assert parsed2["name"] == "extract_web_text"
    assert parsed2["args"]["max_chars"] == 5000


def test_parser_custom_keywords_and_reflection():
    txt = "Thinking: multi-line plan\n- step1\nReflection: retry strategy\nAction: web_search(query='nemo fish')"
    d = ReActTextParser(
        thought_keys=("thinking",),
        reflection_keys=("reflection",),
        action_keys=("action",),
    ).parse(txt)
    assert d.mode == "act"
    assert d.rationale and "multi-line plan" in d.rationale
    assert d.meta.get("reflection") == "retry strategy"
    assert d.actions[0]["name"] == "web_search"

    xml = "<root><think>reasoning</think><reflection>self-check</reflection><action>run_command(command='echo ok')</action></root>"
    d2 = XmlDecisionParser().parse(xml)
    assert d2.mode == "act"
    assert d2.rationale == "reasoning"
    assert d2.meta.get("reflection") == "self-check"
    assert d2.actions[0]["name"] == "run_command"

    js = '{"thinking":"ponder", "reflection":"double check", "action":"write_file(path=\\"x.md\\", content=\\"ok\\")"}'
    d3 = JsonDecisionParser(
        thought_keys=("thinking",),
        reflection_keys=("reflection",),
        action_keys=("action",),
    ).parse(js)
    assert d3.mode == "act"
    assert d3.rationale == "ponder"
    assert d3.meta.get("reflection") == "double check"
    assert d3.actions[0]["name"] == "write_file"
