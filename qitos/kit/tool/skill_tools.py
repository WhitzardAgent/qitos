"""Compatibility shim for skill-related tools.

Prefer importing from `qitos.kit.tool.skill` in new code.
"""

from qitos.kit.tool.skill.toolset import SkillToolSet

__all__ = ["SkillToolSet"]
