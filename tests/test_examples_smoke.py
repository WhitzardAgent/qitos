from __future__ import annotations

from pathlib import Path

from examples._support import FakeTerminal, SequenceModel, local_html_server, write_minimal_epub
from examples.benchmarks.cybench_eval import CyBenchReactAgent
from examples.benchmarks.gaia_eval import OpenDeepResearchGaiaAgent
from examples.benchmarks.tau_bench_eval import TauBenchAgent
from examples.patterns.planact import PlanActAgent
from examples.patterns.react import ReactAgent
from examples.patterns.reflexion import ReflexionAgent
from examples.patterns.tot import ToTAgent
from qitos_zoo.qitos_coder.preset_agent import ClaudeCodeAgent
from examples.real.code_security_audit_agent import CodeSecurityAuditAgent
from examples.real.coding_agent import CodingMemoryReactAgent
from examples.real.computer_use_agent import ComputerUseReActAgent
from examples.real.desktop_env_smoke import main as desktop_env_smoke_main
from examples.real.epub_reader_agent import EpubTreeOfThoughtAgent
from examples.real.openai_cua_agent import main as openai_cua_main
from examples.real.react_compact_agent import CompactReactAgent
from examples.real.research_harness_agent import ResearchHarnessAgent
from examples.real.skillhub_github_agent import GitHubSkillAgent
from examples.real.swe_agent import SWEDynamicPlanningAgent
from examples.real.terminus_2 import Terminus2Agent
from examples.real.visual_inspect_agent import main as visual_inspect_main
from examples.real.whitzard_agent import WhitzardAgent
from qitos.kit import MarkdownFileMemory, TextWebEnv, TmuxEnv
from qitos.demo.minimal import run_minimal_demo


VERIFY_COMMAND = "python -c 'import buggy_module; assert buggy_module.add(20, 22) == 42'"


def _seed_buggy_module(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "buggy_module.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )


def _react_fix_outputs() -> list[str]:
    return [
        'Thought: inspect target\nAction: view(path="buggy_module.py")',
        'Thought: patch logic\nAction: replace_lines(path="buggy_module.py", start_line=2, end_line=2, replacement="    return a + b")',
        f'Thought: verify change\nAction: run_command(command="{VERIFY_COMMAND}")',
        'Final Answer: Fixed and verified.',
    ]


def test_minimal_example_smoke_runs(tmp_path: Path) -> None:
    run_minimal_demo(
        workspace=tmp_path / "playground" / "minimal_coding_agent",
        trace_logdir=tmp_path / "runs",
        render=False,
        llm=SequenceModel(_react_fix_outputs()),
    )


def test_react_planact_coding_and_compact_examples_smoke(tmp_path: Path) -> None:
    for agent_cls in (ReactAgent, CodingMemoryReactAgent, CompactReactAgent):
        workspace = tmp_path / agent_cls.__name__.lower()
        _seed_buggy_module(workspace)
        llm = SequenceModel(_react_fix_outputs())
        if agent_cls is CodingMemoryReactAgent:
            agent = agent_cls(
                llm=llm,
                workspace_root=str(workspace),
                memory=MarkdownFileMemory(path=str(workspace / "memory.md")),
            )
        else:
            agent = agent_cls(llm=llm, workspace_root=str(workspace))
        result = agent.run(
            task="fix",
            workspace=str(workspace),
            max_steps=6,
            render=False,
            trace=False,
            return_state=True,
        )
        assert result.state.stop_reason == "final"

    workspace = tmp_path / "planact"
    _seed_buggy_module(workspace)
    llm = SequenceModel(
        [
            "1. Inspect buggy_module.py\n2. Patch the return line\n3. Run verification",
            'Thought: patch line\nAction: replace_lines(path="buggy_module.py", start_line=2, end_line=2, replacement="    return a + b")',
            f'Thought: verify patch\nAction: run_command(command="{VERIFY_COMMAND}")',
            'Final Answer: Verified.',
        ]
    )
    agent = PlanActAgent(llm=llm, workspace_root=str(workspace))
    result = agent.run(
        task="fix",
        workspace=str(workspace),
        max_steps=6,
        render=False,
        trace=False,
        return_state=True,
    )
    assert result.state.stop_reason == "final"
    assert result.state.plan_steps


def test_reflexion_and_computer_use_examples_smoke(tmp_path: Path) -> None:
    html = "<html><body><h1>Smoke Article</h1><p>This article says testing should stay small and grounded.</p></body></html>"
    with local_html_server(html) as url:
        reflexion = ReflexionAgent(
            llm=SequenceModel(
                [
                    '{"answer":"Testing should stay small and grounded.","citations":[{"source":"source_text","quote":"testing should stay small and grounded"},{"source":"source_text","quote":"Smoke Article"}],"critique":{"missing":[],"superfluous":[],"grounding":["Grounded in source text."],"needs_revision":false}}'
                ]
            )
        )
        reflexion_result = reflexion.run(
            task="summarize",
            workspace=str(tmp_path / "reflexion"),
            target_url=url,
            max_steps=5,
            render=False,
            trace=False,
            return_state=True,
        )
        assert reflexion_result.state.stop_reason == "final"

    workspace = tmp_path / "computer_use"
    workspace.mkdir(parents=True, exist_ok=True)
    agent = ComputerUseReActAgent(
        llm=SequenceModel(
            [
                '```json\n{"mode":"act","rationale":"write a local smoke report","actions":[{"name":"write_file","args":{"path":"report.md","content":"Smoke report\\n\\nLocal smoke content."}}]}\n```',
                '{"mode":"final","rationale":"report written","final_answer":"report.md created"}',
            ]
        ),
        workspace_root=str(workspace),
    )
    result = agent.run(
        task="write report",
        workspace=str(workspace),
        max_steps=4,
        render=False,
        trace=False,
        return_state=True,
    )
    assert result.state.stop_reason == "final"
    assert (workspace / "report.md").exists()


def test_epub_examples_smoke(tmp_path: Path) -> None:
    workspace = tmp_path / "epub"
    epub_path = workspace / "book.epub"
    write_minimal_epub(epub_path)

    tot = ToTAgent(
        llm=SequenceModel(
            [
                '{"thoughts":[{"idea":"search question","score":0.9,"action":{"name":"epub.search","args":{"query":"main argument of chapter 1","top_k":2}}}],"can_answer":false,"answer":""}',
                '{"thoughts":[{"idea":"read first chapter","score":0.95,"action":{"name":"epub.read_chapter","args":{"chapter_index":0,"max_chars":4000}}}],"can_answer":false,"answer":""}',
                '{"thoughts":[],"can_answer":true,"answer":"The main argument of chapter 1 is that tests should be small and reliable."}',
            ]
        ),
        workspace_root=str(workspace),
    )
    tot_result = tot.run(
        task="answer question",
        workspace=str(workspace),
        epub_path="book.epub",
        question="What is the main argument of chapter 1?",
        max_steps=6,
        render=False,
        trace=False,
        return_state=True,
    )
    assert tot_result.state.stop_reason == "final"

    reader = EpubTreeOfThoughtAgent(
        llm=SequenceModel(
            [
                '{"thoughts":[{"idea":"search relevant chapter","score":0.9,"action":{"name":"epub.search","args":{"query":"main argument of chapter 1","top_k":2}}}],"can_answer":false,"answer":""}',
                '{"thoughts":[{"idea":"read first chapter","score":0.95,"action":{"name":"epub.read_chapter","args":{"chapter_index":0,"max_chars":4000}}}],"can_answer":false,"answer":""}',
                '{"thoughts":[],"can_answer":true,"answer":"The main argument of chapter 1 is that tests should be small and reliable."}',
            ]
        ),
        workspace_root=str(workspace),
    )
    reader_result = reader.run(
        task="answer question",
        workspace=str(workspace),
        epub_path="book.epub",
        question="What is the main argument of chapter 1?",
        max_steps=6,
        render=False,
        trace=False,
        return_state=True,
    )
    assert reader_result.state.stop_reason == "final"


def test_visual_inspect_example_smoke_runs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    visual_inspect_main(smoke=True)


def test_openai_cua_and_desktop_env_examples_smoke_runs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    openai_cua_main(smoke=True)
    desktop_env_smoke_main(smoke=True)


def test_swe_claude_security_and_skill_examples_smoke(tmp_path: Path) -> None:
    workspace = tmp_path / "swe"
    _seed_buggy_module(workspace)
    swe = SWEDynamicPlanningAgent(
        llm=SequenceModel(
            [
                "1. Inspect buggy_module.py\n2. Patch the return statement\n3. Run verification",
                '<decision mode="act"><action name="replace_lines"><arg name="path">buggy_module.py</arg><arg name="start_line">2</arg><arg name="end_line">2</arg><arg name="replacement">    return a + b</arg></action></decision>',
                f'<decision mode="act"><action name="run_command"><arg name="command">{VERIFY_COMMAND}</arg></action></decision>',
                '<decision mode="final"><final_answer>Fixed and verified.</final_answer></decision>',
            ]
        ),
        workspace_root=str(workspace),
    )
    swe_result = swe.run(
        task="fix",
        workspace=str(workspace),
        max_steps=6,
        render=False,
        trace=False,
        return_state=True,
    )
    assert swe_result.state.stop_reason == "final"

    research_workspace = tmp_path / "research"
    _seed_buggy_module(research_workspace)
    research = ResearchHarnessAgent(
        llm=SequenceModel(
            [
                '{"thought":"inspect file","action":{"name":"view","args":{"path":"buggy_module.py"}}}',
                '{"thought":"patch file","action":{"name":"replace_lines","args":{"path":"buggy_module.py","start_line":2,"end_line":2,"replacement":"    return a + b"}}}',
                f'{{"thought":"verify patch","action":{{"name":"run_command","args":{{"command":"{VERIFY_COMMAND}"}}}}}}',
                '{"thought":"done","final_answer":"Fixed and verified."}',
            ]
        ),
        workspace_root=str(research_workspace),
        protocol="json_decision_v1",
    )
    research_result = research.run(
        task="fix",
        workspace=str(research_workspace),
        max_steps=6,
        render=False,
        trace=False,
        return_state=True,
    )
    assert research_result.state.stop_reason == "final"

    claude_workspace = tmp_path / "claude"
    _seed_buggy_module(claude_workspace)
    claude = ClaudeCodeAgent(
        llm=SequenceModel(
            [
                'Thought: note plan\nAction: todo_write(todos=[{"content":"Fix buggy_module.py","status":"in_progress"}])',
                'Thought: patch file\nAction: replace_lines(path="buggy_module.py", start_line=2, end_line=2, replacement="    return a + b")',
                f'Thought: verify patch\nAction: run_command(command="{VERIFY_COMMAND}")',
                'Final Answer: Fixed and verified.',
            ]
        ),
        workspace_root=str(claude_workspace),
    )
    claude_result = claude.run(
        task="fix",
        workspace=str(claude_workspace),
        max_steps=6,
        render=False,
        trace=False,
        return_state=True,
    )
    assert claude_result.state.stop_reason == "final"

    audit_workspace = tmp_path / "audit"
    audit_workspace.mkdir(parents=True, exist_ok=True)
    (audit_workspace / "app.py").write_text(
        "from flask import Flask, request\nimport subprocess\n\napp = Flask(__name__)\n@app.route('/run')\ndef run():\n    subprocess.run(request.args.get('cmd'), shell=True)\n    return 'ok'\n",
        encoding="utf-8",
    )
    audit = CodeSecurityAuditAgent(
        llm=SequenceModel(
            [
                'Thought: inventory repo\nAction: audit_inventory()',
                'Thought: rank hotspots\nAction: audit_hotspots()',
                'Final Answer: Potential command injection found and reviewed.',
            ]
        ),
        workspace_root=str(audit_workspace),
    )
    audit_result = audit.run(
        task="audit",
        workspace=str(audit_workspace),
        max_steps=5,
        render=False,
        trace=False,
        return_state=True,
    )
    assert audit_result.state.stop_reason == "final"

    skill_workspace = tmp_path / "skillhub"
    skill_workspace.mkdir(parents=True, exist_ok=True)
    skill_agent = GitHubSkillAgent(
        llm=SequenceModel(['Final Answer: Use the GitHub skill to inspect failed CI runs.']),
        workspace_root=str(skill_workspace),
        bootstrap_github_skill=False,
        allow_runtime_skill_install=False,
    )
    skill_result = skill_agent.run(
        task="explain CI debugging",
        workspace=str(skill_workspace),
        max_steps=2,
        render=False,
        trace=False,
        return_state=True,
    )
    assert skill_result.state.stop_reason == "final"


def test_terminal_examples_smoke(tmp_path: Path) -> None:
    terminal = FakeTerminal()
    env = TmuxEnv(
        workspace_root=str(tmp_path),
        session_name="terminus-smoke",
        terminal=terminal,
        auto_kill=False,
    )
    terminus = Terminus2Agent(
        llm=SequenceModel(
            [
                '<minimax:tool_call><analysis>Check workspace state</analysis><plan>Run pwd</plan><invoke name="send_terminal_keys"><parameter name="keystrokes">pwd</parameter><parameter name="duration_sec">0.1</parameter><parameter name="submit">true</parameter></invoke></minimax:tool_call>',
                '<minimax:response><analysis>Task is complete</analysis><plan>Request completion</plan><task_complete>true</task_complete></minimax:response>',
                '<minimax:response><analysis>Confirmed completion</analysis><plan>Finish</plan><task_complete>true</task_complete></minimax:response>',
            ],
            model="MiniMax-M2.5",
        )
    )
    terminus_result = terminus.run(
        task="check pwd",
        workspace=str(tmp_path),
        env=env,
        max_steps=5,
        render=False,
        trace=False,
        return_state=True,
    )
    assert terminus_result.state.stop_reason == "success"

    repo = tmp_path / "vim"
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "app.py").write_text(
        "from flask import Flask, request\nimport subprocess\napp = Flask(__name__)\n@app.route('/run')\ndef run():\n    subprocess.run(request.args.get('cmd'), shell=True)\n    return 'ok'\n",
        encoding="utf-8",
    )
    whitzard_env = TmuxEnv(
        workspace_root=str(repo),
        session_name="whitzard-smoke",
        terminal=FakeTerminal(),
        auto_kill=False,
    )
    whitzard = WhitzardAgent(
        llm=SequenceModel(
            [
                '<minimax:tool_call><analysis>Inventory repository</analysis><plan>Run audit inventory</plan><invoke name="audit_inventory"></invoke></minimax:tool_call>',
                '<minimax:tool_call><analysis>Rank hotspots</analysis><plan>Run hotspot analysis</plan><invoke name="audit_hotspots"></invoke></minimax:tool_call>',
                '<minimax:tool_call><analysis>Write final report</analysis><plan>Generate markdown report</plan><invoke name="generate_report"><parameter name="format">markdown</parameter><parameter name="output_file">security_report.md</parameter></invoke></minimax:tool_call>',
                '<minimax:response><analysis>Report written</analysis><plan>Request completion</plan><task_complete>true</task_complete></minimax:response>',
                '<minimax:response><analysis>Confirmed completion</analysis><plan>Finish</plan><task_complete>true</task_complete></minimax:response>',
            ],
            model="MiniMax-M2.5",
        ),
        workspace_root=str(repo),
    )
    whitzard_result = whitzard.run(
        task="audit repo",
        workspace=str(repo),
        env=whitzard_env,
        max_steps=8,
        render=False,
        trace=False,
        return_state=True,
    )
    assert whitzard_result.state.stop_reason == "success"
    assert whitzard_result.state.final_report_path == "security_report.md"


def test_benchmark_agent_examples_smoke(tmp_path: Path) -> None:
    gaia_workspace = tmp_path / "gaia"
    gaia_workspace.mkdir(parents=True, exist_ok=True)
    gaia = OpenDeepResearchGaiaAgent(
        llm=SequenceModel(["Final Answer: smoke answer"]),
        workspace_root=str(gaia_workspace),
    )
    gaia_result = gaia.run(
        task="answer benchmark question",
        workspace=str(gaia_workspace),
        env=TextWebEnv(workspace_root=str(gaia_workspace)),
        max_steps=2,
        render=False,
        trace=False,
        return_state=True,
    )
    assert gaia_result.state.stop_reason == "final"

    cybench_workspace = tmp_path / "cybench"
    cybench_workspace.mkdir(parents=True, exist_ok=True)
    cybench = CyBenchReactAgent(
        llm=SequenceModel(["Final Answer: flag{smoke}"]),
        workspace_root=str(cybench_workspace),
    )
    cybench_result = cybench.run(
        task="solve objective",
        workspace=str(cybench_workspace),
        max_steps=2,
        render=False,
        trace=False,
        return_state=True,
    )
    assert cybench_result.state.stop_reason == "final"

    class _Reset:
        def __init__(self):
            self.observation = "Welcome"

    class FakeTauEnv:
        tools_info = []
        wiki = "Smoke wiki"
        rules = ["Be correct."]

        def reset(self, task_index: int = 0):
            _ = task_index
            return _Reset()

        def step(self, action):
            raise AssertionError(f"Unexpected tool execution during smoke: {action}")

    tau = TauBenchAgent(
        llm=SequenceModel(["Final Answer: smoke done"]),
        tau_env=FakeTauEnv(),
    )
    tau_result = tau.run(
        task="solve tau task",
        workspace=str(tmp_path / "tau"),
        max_steps=2,
        render=False,
        trace=False,
        return_state=True,
    )
    assert tau_result.state.stop_reason == "final"
