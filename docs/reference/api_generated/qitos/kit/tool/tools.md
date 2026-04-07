# `qitos.kit.tool.tools`

- Module Group: `qitos.kit`
- Source: [qitos/kit/tool/tools.py](https://github.com/Qitor/qitos/blob/main/qitos/kit/tool/tools.py)

## Quick Jump

- [Classes](#classes)
- [Functions](#functions)
- [Function: `codebase_tools`](#function-codebase-tools)
- [Function: `coding_tools`](#function-coding-tools)
- [Function: `editor_tools`](#function-editor-tools)
- [Function: `math_tools`](#function-math-tools)
- [Function: `notebook_tools`](#function-notebook-tools)
- [Function: `task_tools`](#function-task-tools)
- [Function: `web_tools`](#function-web-tools)

## Classes

- _None_

## Functions

<a id="function-codebase-tools"></a>
???+ note "Function: `codebase_tools(workspace_root: 'str') -> 'ToolRegistry'`"
    Build a registry for code search plus basic file read and write tools.

<a id="function-coding-tools"></a>
???+ note "Function: `coding_tools(workspace_root: 'str', shell_timeout: 'int' = 30, include_notebook: 'bool' = True) -> 'ToolRegistry'`"
    Build a registry with the standard coding-oriented tool bundle.

<a id="function-editor-tools"></a>
???+ note "Function: `editor_tools(workspace_root: 'str') -> 'ToolRegistry'`"
    Build a registry containing only the editor toolset.

<a id="function-math-tools"></a>
???+ note "Function: `math_tools() -> 'ToolRegistry'`"
    Build a tiny registry of arithmetic example tools.

<a id="function-notebook-tools"></a>
???+ note "Function: `notebook_tools(workspace_root: 'str') -> 'ToolRegistry'`"
    Build a registry containing notebook-specific tools.

<a id="function-task-tools"></a>
???+ note "Function: `task_tools(workspace_root: 'str', board_relpath: 'str' = '.qitos/task_board.json') -> 'ToolRegistry'`"
    Build a registry containing the external task-board tools.

<a id="function-web-tools"></a>
???+ note "Function: `web_tools() -> 'ToolRegistry'`"
    Build a registry containing HTTP and HTML extraction tools.

## Source Index

- [qitos/kit/tool/tools.py](https://github.com/Qitor/qitos/blob/main/qitos/kit/tool/tools.py)