"""Provider-aware tools for agents to self-manage skills at runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from qitos.core.tool import tool
from qitos.kit.skill import SkillManager, SkillRegistry


class SkillToolSet:
    """Toolset for searching, installing, and activating skills."""

    name = ""
    version = "2.0"

    def __init__(
        self,
        registry: Optional[SkillRegistry] = None,
        manager: Optional[SkillManager] = None,
        workspace_root: Optional[str] = None,
        default_provider: str = "skillhub",
    ):
        self.workspace_root = workspace_root
        self.registry = registry
        self._manager = manager
        self.default_provider = default_provider

    @property
    def manager(self) -> SkillManager:
        if self._manager is None:
            self._manager = SkillManager(
                workspace_root=self.workspace_root,
                registry=self.registry,
                default_provider=self.default_provider,
            )
        return self._manager

    def tools(self) -> List[Any]:
        return [
            self.check_skill_hub,
            self.install_skill_hub,
            self.search_skills,
            self.install_skill,
            self.activate_skill,
            self.list_installed_skills,
            self.get_skill_info,
        ]

    @tool(name="check_skill_hub")
    def check_skill_hub(self, runtime_context: Optional[dict[str, Any]] = None) -> str:
        """
        Report whether the configured skill provider is available for use.

        :param runtime_context: Optional runtime context with env information.
        """
        manager = self._manager_from_runtime(runtime_context)
        return (
            f"Skill provider '{manager.default_provider}' is configured and ready. "
            "Use search_skills or install_skill to work with third-party skills."
        )

    @tool(name="install_skill_hub")
    def install_skill_hub(
        self, hub_url: str, runtime_context: Optional[dict[str, Any]] = None
    ) -> str:
        """
        Install a skill hub manifest from a local or remote provider URL.

        :param hub_url: Provider manifest location.
        :param runtime_context: Optional runtime context with env information.
        """
        manager = self._manager_from_runtime(runtime_context)
        installed = manager.install(f"local:{hub_url}", activate=False)
        return f"Installed hub manifest '{installed.manifest.name}' from {hub_url}."

    @tool(name="search_skills")
    def search_skills(
        self,
        query: str,
        provider: str = "",
        limit: int = 5,
        runtime_context: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Search the configured skill provider for installable skills.

        :param query: Search query text.
        :param provider: Optional provider override.
        :param limit: Maximum number of results to return.
        :param runtime_context: Optional runtime context with env information.
        """
        manager = self._manager_from_runtime(runtime_context)
        results = manager.search(query=query, provider=provider or None, limit=limit)
        if not results:
            return f"No skills found for query '{query}'."
        lines = []
        for result in results:
            version = result.version or "-"
            lines.append(f"{result.ref} (v{version}): {result.description}")
        return "\n".join(lines)

    @tool(name="install_skill")
    def install_skill(
        self,
        skill_ref: str = "",
        skill_name: str = "",
        activate: bool = True,
        runtime_context: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Install one skill by reference and optionally activate it immediately.

        :param skill_ref: Fully qualified skill reference.
        :param skill_name: Alternate plain skill name if no reference is provided.
        :param activate: Whether the installed skill should be activated.
        :param runtime_context: Optional runtime context with env information.
        """
        manager = self._manager_from_runtime(runtime_context)
        resolved_ref = skill_ref or skill_name
        installed = manager.install(resolved_ref, activate=activate)
        state = "activated" if installed.active else "installed"
        return f"Successfully {state} skill '{installed.key}' v{installed.package.version}. {installed.manifest.description}"

    @tool(name="activate_skill")
    def activate_skill(
        self,
        skill_ref: str,
        runtime_context: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Activate one already-installed skill so it can be injected into agents.

        :param skill_ref: Installed skill reference.
        :param runtime_context: Optional runtime context with env information.
        """
        manager = self._manager_from_runtime(runtime_context)
        if manager.activate(skill_ref):
            return f"Activated skill '{skill_ref}'."
        return f"Skill '{skill_ref}' is not installed."

    @tool(name="list_installed_skills")
    def list_installed_skills(
        self, runtime_context: Optional[dict[str, Any]] = None
    ) -> str:
        """
        List all skills installed in the current workspace context.

        :param runtime_context: Optional runtime context with env information.
        """
        manager = self._manager_from_runtime(runtime_context)
        installed = manager.list_installed()
        if not installed:
            return "No skills are currently installed."
        lines = ["Installed skills:"]
        for item in installed:
            active = " [active]" if item.active else ""
            lines.append(f"- {item.key}{active}: {item.manifest.description}")
        return "\n".join(lines)

    @tool(name="get_skill_info")
    def get_skill_info(
        self,
        skill_ref: str = "",
        skill_name: str = "",
        runtime_context: Optional[dict[str, Any]] = None,
    ) -> str:
        """
        Describe one installed or discoverable skill by reference.

        :param skill_ref: Fully qualified skill reference.
        :param skill_name: Alternate plain skill name if no reference is provided.
        :param runtime_context: Optional runtime context with env information.
        """
        manager = self._manager_from_runtime(runtime_context)
        resolved_ref = skill_ref or skill_name
        installed = manager.get_installed(resolved_ref)
        if installed is not None:
            lines = [
                f"Skill: {installed.key}",
                f"Version: {installed.package.version}",
                f"Description: {installed.manifest.description}",
                f"Active: {installed.active}",
                f"Install Path: {installed.install_path}",
            ]
            if installed.package.homepage:
                lines.append(f"Homepage: {installed.package.homepage}")
            return "\n".join(lines)
        described = manager.describe(resolved_ref)
        if described is None:
            return f"Skill '{resolved_ref}' was not found."
        lines = [
            f"Skill: {described.ref}",
            f"Version: {described.version or '-'}",
            f"Description: {described.description}",
        ]
        if described.homepage:
            lines.append(f"Homepage: {described.homepage}")
        return "\n".join(lines)

    def _manager_from_runtime(
        self, runtime_context: Optional[dict[str, Any]]
    ) -> SkillManager:
        runtime_context = runtime_context or {}
        env = runtime_context.get("env")
        workspace_root = self.workspace_root
        if workspace_root is None and env is not None:
            workspace_root = getattr(env, "workspace_root", None)
        if self._manager is None or (
            workspace_root
            and Path(workspace_root).resolve()
            != Path(self.manager.workspace_root or ".").resolve()
        ):
            self._manager = SkillManager(
                workspace_root=workspace_root,
                registry=self.registry,
                default_provider=self.default_provider,
            )
        return self._manager
