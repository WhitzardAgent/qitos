"""Integration with QitOS AgentModule for easy skill usage."""

from __future__ import annotations

from typing import List, Optional, TypeVar

from qitos.core.agent_module import AgentModule

from .injector import SkillInjector, SkillPromptBuilder
from .loader import SkillInstaller
from .manager import SkillManager
from .manifest import SkillManifest
from .registry import SkillRegistry


StateT = TypeVar("StateT")
ObservationT = TypeVar("ObservationT")
ActionT = TypeVar("ActionT")


class SkillMixin:
    """Mixin for AgentModule to add third-party skill support."""

    def __init__(self, *args, **kwargs):
        workspace_root = kwargs.pop("workspace_root", None)
        skill_sources = list(kwargs.pop("skill_sources", []) or [])
        active_skills = list(kwargs.pop("active_skills", []) or [])
        allow_runtime_skill_install = bool(
            kwargs.pop("allow_runtime_skill_install", False)
        )
        default_skill_provider = kwargs.pop("default_skill_provider", "skillhub")
        super().__init__(*args, **kwargs)
        self._skill_registry: Optional[SkillRegistry] = None
        self._skill_installer: Optional[SkillInstaller] = None
        self._skill_injector: Optional[SkillInjector] = None
        self._skill_manager: Optional[SkillManager] = None
        self._active_skills: List[str] = []
        self._workspace_root = workspace_root
        self._allow_runtime_skill_install = allow_runtime_skill_install
        self._default_skill_provider = default_skill_provider
        self._bootstrap_skill_sources = skill_sources
        self._bootstrap_active_skills = active_skills

        if skill_sources:
            self.skill_manager.ensure(skill_sources, activate=False)
        for skill_ref in active_skills:
            self.skill_manager.activate(skill_ref)
            if skill_ref not in self._active_skills:
                self._active_skills.append(skill_ref)

        if (
            allow_runtime_skill_install
            and getattr(self, "tool_registry", None) is not None
        ):
            try:
                from qitos.kit.tool.skill import SkillToolSet

                self.tool_registry.register_toolset(
                    SkillToolSet(
                        manager=self.skill_manager, workspace_root=self._workspace_root
                    )
                )
            except Exception:
                pass

    @property
    def skill_registry(self) -> SkillRegistry:
        if self._skill_registry is None:
            self._skill_registry = SkillRegistry(workspace_root=self._workspace_root)
        return self._skill_registry

    @property
    def skill_manager(self) -> SkillManager:
        if self._skill_manager is None:
            self._skill_manager = SkillManager(
                workspace_root=self._workspace_root,
                registry=self.skill_registry,
                default_provider=self._default_skill_provider,
            )
        return self._skill_manager

    @property
    def skill_installer(self) -> SkillInstaller:
        if self._skill_installer is None:
            self._skill_installer = SkillInstaller(
                registry=self.skill_registry,
                workspace_root=self._workspace_root,
                default_provider=self._default_skill_provider,
            )
        return self._skill_installer

    @property
    def skill_injector(self) -> SkillInjector:
        if self._skill_injector is None:
            self._skill_injector = SkillInjector()
        return self._skill_injector

    def ensure_skillhub(self, hub_url: str) -> SkillManifest:
        return self.skill_installer.ensure_hub(hub_url)

    def install_skill(self, source: str, name: Optional[str] = None) -> SkillManifest:
        ref = source
        if (
            name
            and ":" not in source
            and not source.startswith(("http://", "https://"))
        ):
            ref = f"{self.skill_manager.default_provider}:{name}"
        installed = self.skill_manager.install(ref)
        return installed.manifest

    def install_skill_via_hub(self, hub_url: str, skill_name: str) -> SkillManifest:
        _ = hub_url
        installed = self.skill_manager.install(
            f"{self.skill_manager.default_provider}:{skill_name}"
        )
        return installed.manifest

    def activate_skill(self, name: str) -> bool:
        if self.skill_manager.activate(name):
            if name not in self._active_skills:
                self._active_skills.append(name)
            return True
        return False

    def deactivate_skill(self, name: str) -> None:
        self.skill_manager.deactivate(name)
        if name in self._active_skills:
            self._active_skills.remove(name)

    def build_prompt_with_skills(
        self,
        base_prompt: str,
        task: str = "",
        auto_select: bool = False,
        filepath: Optional[str] = None,
    ) -> str:
        builder = SkillPromptBuilder(self.skill_registry)
        for skill_name in self._active_skills:
            builder.with_skill(skill_name)
        if auto_select:
            builder.with_skills_for_task(task, filepath)
        return builder.build(base_prompt, task)

    def list_installed_skills(self) -> List[SkillManifest]:
        return self.skill_registry.list_skills()

    def get_skill(self, name: str) -> Optional[SkillManifest]:
        return self.skill_manager.get_skill(name)


class SkilledAgent(SkillMixin, AgentModule[StateT, ObservationT, ActionT]):
    """Base agent class with built-in skill support."""

    def __init__(
        self,
        skillhub_url: Optional[str] = None,
        auto_install_skills: Optional[List[str]] = None,
        skill_sources: Optional[List[str]] = None,
        active_skills: Optional[List[str]] = None,
        allow_runtime_skill_install: bool = False,
        workspace_root: Optional[str] = None,
        *args,
        **kwargs,
    ):
        skill_sources = list(skill_sources or [])
        if auto_install_skills:
            skill_sources.extend([f"skillhub:{slug}" for slug in auto_install_skills])
        super().__init__(
            *args,
            workspace_root=workspace_root,
            skill_sources=skill_sources,
            active_skills=active_skills or auto_install_skills or [],
            allow_runtime_skill_install=allow_runtime_skill_install,
            **kwargs,
        )
        if skillhub_url:
            self.ensure_skillhub(skillhub_url)
