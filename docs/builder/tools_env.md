# Tools & Env

## Goal

Build portable agents where action intent is stable but execution backend can change.

## Mental model

- **Tool**: semantic operation (`read_file`, `replace_lines`, `run_command`, `fetch_url`).
- **Env**: execution backend implementing capability ops (`file`, `process`, etc.).

## Tutorial: one tool, multiple env backends

1. Define/register a tool requiring ops group `file`.
2. Run with `HostEnv`.
3. Run with `DockerEnv` (same tool, different backend).
4. Verify behavior parity via trace.

## Practical rules

1. Keep tool inputs/outputs structured and explicit.
2. Fail early on missing required ops.
3. Never hide backend assumptions inside parser or prompts.
4. Keep side effects localized to env ops layer.
5. Write tool docstrings in the canonical QiTOS style because those docstrings
   become the model-visible tool descriptions.

## Tool Authoring Style

All public tools in `qitos.kit.tool` follow one documentation contract:

- first line: what the tool does
- `:param ...:` lines: what each argument means
- final note: constraints, side effects, or useful behavior hints

Example:

```python
@tool(name="create")
def create(path: str, file_text: str = "") -> dict:
    """
    Create a new file with the given content.

    :param path: Path relative to the workspace root (e.g., `new_file.py`).
    :param file_text: Content to write to the new file.

    Automatically creates parent directories if they don't exist.
    """
```

QiTOS reads this callable docstring directly when assembling tool descriptions
for the model.

## Predefined kits you can use directly

Tool kits (`qitos.kit.tool`):

- `EditorToolSet`, `EpubToolSet`
- `WriteFile`, `ReadFile`, `ListFiles`
- `RunCommand`
- `HTTPRequest`, `HTTPGet`, `HTTPPost`, `HTMLExtractText`
- `WebSearch`, `VisitURL`, `PageDown`, `PageUp`, `FindInPage`, `FindNext`, `ArchiveSearch`
- `ThinkingToolSet`

Planning kits (`qitos.kit.planning`):
- `NumberedPlanBuilder`
- `DynamicTreeSearch`
- `format_action`

See full details in:

- [Tools & ToolRegistry (Reference)](../reference/tools.md)

## Troubleshooting

1. `ENV_CAPABILITY_MISMATCH`:
- tool required ops are missing in current env.

2. action succeeds in host but fails in docker:
- path mapping or workspace root mismatch.

3. command tool unstable:
- tighten timeout and sanitize command template.

## Source Index

- [qitos/core/tool.py](https://github.com/Qitor/qitos/blob/main/qitos/core/tool.py)
- [qitos/core/env.py](https://github.com/Qitor/qitos/blob/main/qitos/core/env.py)
- [qitos/engine/action_executor.py](https://github.com/Qitor/qitos/blob/main/qitos/engine/action_executor.py)
- [qitos/kit/env/host_env.py](https://github.com/Qitor/qitos/blob/main/qitos/kit/env/host_env.py)
- [qitos/kit/env/docker_env.py](https://github.com/Qitor/qitos/blob/main/qitos/kit/env/docker_env.py)
- [qitos/kit/tool/editor.py](https://github.com/Qitor/qitos/blob/main/qitos/kit/tool/editor.py)
