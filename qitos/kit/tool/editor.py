"""Workspace-local editor tools for reading and modifying files safely."""

import os
import re
import ast
from typing import Any, Dict, List, Optional

from difflib import unified_diff, SequenceMatcher
from pathlib import Path

from qitos.core.tool import tool









class EditorToolSet:
    """Bundle high-signal file editing tools for coding and document agents."""
    
    def __init__(self, workspace_root: str = "."):
        """Create an editor toolset rooted at one workspace directory."""
        self._workspace_root = os.path.abspath(workspace_root)

    def setup(self, context: Dict[str, Any]) -> None:
        """Prepare editor resources before runtime starts."""
        _ = context

    def teardown(self, context: Dict[str, Any]) -> None:
        """Release editor resources after runtime ends."""
        _ = context

    def tools(self) -> List[Any]:
        """Return the public editor tools in their canonical registration order."""
        return [
            self.view,
            self.create,
            self.str_replace,
            self.insert,
            self.search,
            self.list_tree,
            self.replace_lines,
        ]
    
    def _resolve_path(self, path: str) -> str:
        """
        Resolve path relative to workspace_root with security check.
        
        All input paths are treated as relative to workspace_root.
        Uses resolve() to get absolute path and verifies it's within workspace_root.
        Raises PermissionError if path attempts to escape workspace.
        
        :param path: Relative path to resolve
        :return: Absolute resolved path
        """
        workspace_resolved = Path(self._workspace_root).resolve()
        
        relative_path = path.lstrip("/")
        abs_path = (workspace_resolved / relative_path).resolve()
        
        if not str(abs_path).startswith(str(workspace_resolved) + os.sep) and abs_path != workspace_resolved:
            raise PermissionError(f"Access denied: '{path}' resolves to '{abs_path}' which is outside workspace '{self._workspace_root}'")
        
        return str(abs_path)
    
    def _generate_unified_diff(self, old_content: str, new_content: str, path: str) -> str:
        """Generate unified diff between old and new content."""
        old_lines = old_content.splitlines(keepends=True)
        new_lines = new_content.splitlines(keepends=True)
        
        diff = list(unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3
        ))
        
        if not diff:
            return ""
        
        return "```diff\n" + "".join(diff) + "```"
    
    def _parse_python_error(self, error: SyntaxError, content: str) -> Dict[str, Any]:
        """Parse Python syntax error and provide structured diagnostic feedback."""
        lineno = error.lineno if error.lineno else 1
        offset = error.offset if error.offset else 0
        msg = str(error.args[0]) if error.args else "Unknown syntax error"
        
        lines = content.split("\n")
        error_line = ""
        if 1 <= lineno <= len(lines):
            error_line = lines[lineno - 1].rstrip()
            caret = " " * (offset - 1) + "^"
            error_line += f"\n{caret}"
        
        suggestions = []
        if "unexpected EOF" in msg:
            suggestions.append("Check for missing closing parenthesis, bracket, or quote")
        elif "invalid syntax" in msg:
            suggestions.append("Check for missing colons after function/class definitions")
        elif "EOL" in msg:
            suggestions.append("Check for unclosed strings or missing line continuation")
        elif "indentation" in msg.lower():
            suggestions.append("Ensure consistent indentation (use spaces, not tabs)")
        elif "unexpected indent" in msg:
            suggestions.append("Check for incorrect indentation level")
        else:
            suggestions.append("Review the code around the error line for syntax issues")
        
        return {
            "line_number": lineno,
            "error_line": error_line,
            "message": msg,
            "suggestions": suggestions
        }
    
    def _find_similar_strings(self, target: str, content: str) -> List[str]:
        """Find similar strings in content that might be what user meant."""
        lines = content.split("\n")
        suggestions = []
        
        for i, line in enumerate(lines):
            line_normalized = line.strip()
            target_normalized = target.strip()
            if line_normalized and target_normalized:
                ratio = SequenceMatcher(None, line_normalized, target_normalized).ratio()
                if ratio > 0.7 and ratio < 1.0:
                    suggestions.append(f"Line {i + 1}: {line.strip()}")
        
        return suggestions[:5]
    
    @tool(name='view')
    def view(self, path: str, view_range: Optional[List[int]] = None) -> Dict[str, Any]:
        """
        View a file or directory under the workspace root.

        :param path: Path relative to the workspace root (e.g., `src/main.py` or `src/`).
        :param view_range: Optional inclusive line range `[start, end]` to show for files.

        For files, returns line-numbered content in a fenced code block. For
        directories, returns a readable listing of immediate child entries.
        """
        try:
            resolved_path = self._resolve_path(path)
            
            if os.path.isdir(resolved_path):
                # List directory contents
                items = []
                for item in os.listdir(resolved_path):
                    if not item.startswith('.'):
                        items.append(item)
                items.sort()
                
                output = f"### 📁 Directory: {path}\n\n"
                for item in items[:50]:
                    full_path = os.path.join(resolved_path, item)
                    item_type = "📁" if os.path.isdir(full_path) else "📄"
                    output += f"{item_type} {item}\n"
                
                if len(items) > 50:
                    output += f"\n... and {len(items) - 50} more items"
                
                return {"status": "success", "stdout": output}
            
            # Read file content
            with open(resolved_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
            
            total_lines = len(file_content.split("\n"))
            
            if view_range and isinstance(view_range, list) and len(view_range) == 2:
                start, end = view_range
                lines = file_content.split("\n")
                n_lines = len(lines)
                if start < 1 or start > n_lines:
                    return {"status": "error", "message": f"Invalid view_range start: {start}. File has {n_lines} lines."}
                if end > n_lines:
                    end = n_lines
                if end != -1 and end < start:
                    return {"status": "error", "message": f"Invalid view_range: end {end} < start {start}."}
                if end == -1:
                    file_content = "\n".join(lines[start - 1:])
                else:
                    file_content = "\n".join(lines[start - 1:end])
            
            lines = file_content.split("\n")
            numbered = "\n".join([f"{i + 1}\t{line}" for i, line in enumerate(lines)])
            
            ext = path.split('.')[-1] if '.' in path else ""
            lang_map = {
                "py": "python", "js": "javascript", "ts": "typescript",
                "json": "json", "md": "markdown", "html": "html",
                "css": "css", "sql": "sql", "sh": "bash", "yaml": "yaml"
            }
            lang = lang_map.get(ext, "text")
            
            output = f"### 📄 File: {path} ({total_lines} lines total)\n\n```{lang}\n{numbered}\n```"
            return {"status": "success", "stdout": output}
            
        except FileNotFoundError:
            return {"status": "error", "message": f"File not found: {path}"}
        except PermissionError as e:
            return {"status": "error", "message": str(e)}
        except Exception as e:
            return {"status": "error", "message": f"Error viewing file: {str(e)}"}
    
    @tool(name='create')
    def create(self, path: str, file_text: str = "") -> Dict[str, Any]:
        """
        Create a new file with the given content.

        :param path: Path relative to the workspace root (e.g., `new_file.py`).
        :param file_text: Content to write to the new file.

        Automatically creates parent directories if they don't exist.
        """
        try:
            resolved_path = self._resolve_path(path)
            
            # Create parent directories
            dir_path = os.path.dirname(resolved_path)
            if dir_path and dir_path != '.':
                os.makedirs(dir_path, exist_ok=True)
            
            # Write file
            with open(resolved_path, 'w', encoding='utf-8') as f:
                f.write(file_text)
            
            return {"status": "success", "stdout": f"File created: {path}"}
            
        except PermissionError as e:
            return {"status": "error", "message": str(e)}
        except Exception as e:
            return {"status": "error", "message": f"Error creating file: {str(e)}"}
    
    @tool(name='str_replace')
    def str_replace(self, path: str, old_str: str, new_str: str = "") -> Dict[str, Any]:
        """
        Replace one unique string fragment in a file.

        :param path: Path relative to the workspace root (e.g., `src/main.py`).
        :param old_str: The exact string to replace. Must be unique in the file.
        :param new_str: The new string to replace old_str with.

        Include 1 to 2 lines of surrounding context in `old_str` whenever
        possible so the match is unique and stable. Returns a unified diff after
        a successful edit.
        """
        try:
            resolved_path = self._resolve_path(path)
            
            # Read file
            with open(resolved_path, 'r', encoding='utf-8') as f:
                old_content = f.read()
            
            # Check occurrences
            occurrences = old_content.count(old_str)
            if occurrences == 0:
                similar = self._find_similar_strings(old_str, old_content)
                error_msg = f"No occurrence of old_str found in {path}."
                if similar:
                    error_msg += f"\n\nDid you mean...\n" + "\n".join([f"- {s}" for s in similar])
                return {"status": "error", "message": error_msg}
            elif occurrences > 1:
                lines_with_occurrence = []
                for i, line in enumerate(old_content.split("\n")):
                    if old_str in line:
                        lines_with_occurrence.append(i + 1)
                return {"status": "error", "message": f"Multiple occurrences of old_str in lines {lines_with_occurrence}. Must be unique."}
            
            # Replace
            new_content = old_content.replace(old_str, new_str)
            
            # Validate Python syntax if it's a Python file
            if path.endswith(".py"):
                try:
                    ast.parse(new_content)
                except SyntaxError as e:
                    diag = self._parse_python_error(e, new_content)
                    return {
                        "status": "error",
                        "message": f"Python syntax error at line {diag['line_number']}: {diag['message']}",
                        "diagnostic": diag
                    }
            
            # Write file
            with open(resolved_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            # Generate diff
            diff_output = self._generate_unified_diff(old_content, new_content, path)
            
            output = f"File updated: {path}"
            if diff_output:
                output += f"\n\n### 📝 Applied Changes (Unified Diff):\n{diff_output}"
            
            return {"status": "success", "stdout": output}
            
        except FileNotFoundError:
            return {"status": "error", "message": f"File not found: {path}"}
        except PermissionError as e:
            return {"status": "error", "message": str(e)}
        except Exception as e:
            return {"status": "error", "message": f"Error replacing text: {str(e)}"}
    
    @tool(name='insert')
    def insert(self, path: str, insert_line: int, new_str: str) -> Dict[str, Any]:
        """
        Insert new text after a given line number.

        :param path: Path relative to the workspace root (e.g., `src/main.py`).
        :param insert_line: Line number AFTER which to insert new_str (1-indexed).
        :param new_str: String to insert.

        Returns a unified diff after a successful insertion.
        """
        try:
            resolved_path = self._resolve_path(path)
            
            # Read file
            with open(resolved_path, 'r', encoding='utf-8') as f:
                old_content = f.read()
            
            lines = old_content.split("\n")
            n_lines = len(lines)
            
            if insert_line < 0 or insert_line > n_lines:
                return {"status": "error", "message": f"Invalid insert_line: {insert_line}. File has {n_lines} lines."}
            
            # Insert
            new_lines = lines[:insert_line] + [new_str] + lines[insert_line:]
            new_content = "\n".join(new_lines)
            
            # Validate Python syntax if it's a Python file
            if path.endswith(".py"):
                try:
                    ast.parse(new_content)
                except SyntaxError as e:
                    diag = self._parse_python_error(e, new_content)
                    return {
                        "status": "error",
                        "message": f"Python syntax error at line {diag['line_number']}: {diag['message']}",
                        "diagnostic": diag
                    }
            
            # Write file
            with open(resolved_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            # Generate diff
            diff_output = self._generate_unified_diff(old_content, new_content, path)
            
            output = f"Inserted after line {insert_line} in {path}"
            if diff_output:
                output += f"\n\n### 📝 Applied Changes (Unified Diff):\n{diff_output}"
            
            return {"status": "success", "stdout": output}
            
        except FileNotFoundError:
            return {"status": "error", "message": f"File not found: {path}"}
        except PermissionError as e:
            return {"status": "error", "message": str(e)}
        except Exception as e:
            return {"status": "error", "message": f"Error inserting text: {str(e)}"}
    

    @tool(name='search')
    def search(self, path: str, keyword: str) -> Dict[str, Any]:
        """
        Search for a keyword inside files within a directory tree.

        :param path: Directory path relative to the workspace root (e.g., `src`).
        :param keyword: Keyword to search for.

        Returns matching file paths and line numbers. Only the first tranche of
        matches is shown, so refine the keyword if results are too broad.
        """
        try:
            resolved_path = self._resolve_path(path)
            
            if not keyword or not keyword.strip():
                return {"status": "error", "message": "Keyword cannot be empty."}
            
            matches = []
            
            # Walk through directory
            for root, dirs, files in os.walk(resolved_path):
                # Skip hidden directories and common non-source directories
                dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ['__pycache__', 'node_modules', '.venv', '.git']]
                
                for file in files:
                    if file.startswith('.') or file.endswith(('.pyc', '.egg-info')):
                        continue
                    
                    file_path = os.path.join(root, file)
                    rel_path = os.path.relpath(file_path, self._workspace_root)
                    
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                            for line_num, line in enumerate(f, 1):
                                if keyword in line:
                                    matches.append(f"{rel_path}:{line_num}: {line.rstrip()}")
                                    if len(matches) >= 15:
                                        break
                    except Exception:
                        continue
                    
                    if len(matches) >= 15:
                        break
                
                if len(matches) >= 15:
                    break
            
            if not matches:
                return {"status": "success", "stdout": f"No matches found for '{keyword}' in {path}"}
            
            total_matches = len(matches)
            display_matches = matches[:10]
            display_output = "\n".join(display_matches)
            
            output = f"### 🔍 Search Results for '{keyword}' in {path}\n\n{display_output}"
            
            if total_matches > 10:
                output += f"\n\nShowing first 10 matches, please refine your keyword if needed. (Total: {total_matches}+ matches)"
            
            return {"status": "success", "stdout": output}
            
        except PermissionError as e:
            return {"status": "error", "message": str(e)}
        except Exception as e:
            return {"status": "error", "message": f"Error searching: {str(e)}"}
    

    @tool(name='list_tree')
    def list_tree(self, path: str = ".", depth: int = 3) -> Dict[str, Any]:
        """
        List directory structure in a tree format.

        :param path: Directory path relative to the workspace root (e.g., `src`). Defaults to the workspace root.
        :param depth: Maximum depth to traverse. Defaults to 3.

        Returns a compact tree-style representation of the directory structure.
        """
        try:
            resolved_path = self._resolve_path(path)
            
            if depth < 1:
                depth = 1
            if depth > 10:
                depth = 10
            
            if not os.path.isdir(resolved_path):
                return {"status": "error", "message": f"Path is not a directory: {path}"}
            
            # Build tree
            tree_lines = [f"### 🌳 Directory Tree: {path} (depth: {depth})\n"]
            prefix = os.path.basename(resolved_path) or resolved_path
            tree_lines.append(f"{prefix}/")
            
            def build_tree(current_path: str, indent: str, current_depth: int):
                if current_depth >= depth:
                    return
                
                try:
                    items = os.listdir(current_path)
                except PermissionError:
                    return
                
                # Separate directories and files
                dirs = []
                files = []
                for item in items:
                    if item.startswith('.'):
                        continue
                    full_path = os.path.join(current_path, item)
                    if os.path.isdir(full_path):
                        dirs.append(item)
                    else:
                        files.append(item)
                
                dirs.sort()
                files.sort()
                sorted_items = dirs + files
                
                for i, item in enumerate(sorted_items):
                    is_last = (i == len(sorted_items) - 1)
                    connector = "└── " if is_last else "├── "
                    tree_lines.append(f"{indent}{connector}{item}")
                    
                    full_path = os.path.join(current_path, item)
                    if os.path.isdir(full_path):
                        extension = "    " if is_last else "│   "
                        build_tree(full_path, indent + extension, current_depth + 1)
            
            build_tree(resolved_path, "", 1)
            
            output = "\n".join(tree_lines)
            return {"status": "success", "stdout": output}
            
        except PermissionError as e:
            return {"status": "error", "message": str(e)}
        except Exception as e:
            return {"status": "error", "message": f"Error listing tree: {str(e)}"}
    
    @tool(name='replace_lines')
    def replace_lines(self, path: str, start_line: int, end_line: int, replacement: str = "") -> Dict[str, Any]:
        """
        Replace an inclusive line range with new content.

        Useful when `str_replace` fails because of whitespace differences or when
        the edit is easier to express as a line span.

        :param path: Path relative to the workspace root (e.g., `src/main.py`).
        :param start_line: Starting line number (1-indexed, must be > 0).
        :param end_line: Ending line number (inclusive, must be >= start_line).
        :param replacement: Text to replace the specified lines with.

        Returns a unified diff showing the applied change.
        """
        try:
            if start_line <= 0:
                return {"status": "error", "message": f"start_line must be > 0, got {start_line}."}
            if end_line < start_line:
                return {"status": "error", "message": f"end_line ({end_line}) must be >= start_line ({start_line})."}
            
            resolved_path = self._resolve_path(path)
            
            # Read file
            with open(resolved_path, 'r', encoding='utf-8') as f:
                old_content = f.read()
            
            lines = old_content.split("\n")
            n_lines = len(lines)
            
            if start_line > n_lines:
                return {"status": "error", "message": f"start_line {start_line} exceeds file length ({n_lines} lines)."}
            if end_line > n_lines:
                return {"status": "error", "message": f"end_line {end_line} exceeds file length ({n_lines} lines)."}
            
            # Replace lines
            new_lines = lines[:start_line - 1] + [replacement] + lines[end_line:]
            new_content = "\n".join(new_lines)
            
            # Write file
            with open(resolved_path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            
            # Generate diff
            diff_output = self._generate_unified_diff(old_content, new_content, path)
            
            output = f"Lines {start_line}-{end_line} replaced in {path}"
            if diff_output:
                output += f"\n\n### 📝 Applied Changes (Unified Diff):\n{diff_output}"
            
            return {"status": "success", "stdout": output}
            
        except FileNotFoundError:
            return {"status": "error", "message": f"File not found: {path}"}
        except PermissionError as e:
            return {"status": "error", "message": str(e)}
        except Exception as e:
            return {"status": "error", "message": f"Error replacing lines: {str(e)}"}
