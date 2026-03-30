"""Plugin registry with dependency resolution and feature toggles."""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class PluginInfo:
    name: str
    depends: list[str] = field(default_factory=list)
    provides: list[str] = field(default_factory=list)
    default: bool = True
    description: str = ""
    run: Callable | None = None
    module: Any = None


class Registry:
    """Discovers, loads, and manages plugins with dependency-aware toggling."""

    def __init__(self):
        self._plugins: dict[str, PluginInfo] = {}
        self._enabled: set[str] = set()
        self._mode_map: dict[str, str] = {}  # mode name → plugin name

    @property
    def plugins(self) -> dict[str, PluginInfo]:
        return dict(self._plugins)

    @property
    def enabled(self) -> set[str]:
        return set(self._enabled)

    def discover(self, package_path: str = "tempo.plugins") -> None:
        """Auto-discover plugins from the plugins package."""
        pkg = importlib.import_module(package_path)
        for importer, name, ispkg in pkgutil.iter_modules(pkg.__path__):
            if not ispkg:
                continue
            self.load(f"{package_path}.{name}")

    def load(self, module_path: str) -> PluginInfo | None:
        """Load a single plugin module."""
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            return None

        meta = getattr(mod, "PLUGIN", None)
        if not meta or not isinstance(meta, dict):
            return None

        info = PluginInfo(
            name=meta["name"],
            depends=meta.get("depends", []),
            provides=meta.get("provides", []),
            default=meta.get("default", True),
            description=meta.get("description", ""),
            run=getattr(mod, "run", None),
            module=mod,
        )

        self._plugins[info.name] = info
        for mode in info.provides:
            self._mode_map[mode] = info.name

        if info.default:
            self.enable(info.name)

        return info

    def enable(self, name: str) -> list[str]:
        """Enable a plugin and all its dependencies. Returns list of newly enabled."""
        if name not in self._plugins:
            return []
        if name in self._enabled:
            return []

        newly_enabled = []
        plugin = self._plugins[name]

        # Enable dependencies first
        for dep in plugin.depends:
            newly_enabled.extend(self.enable(dep))

        self._enabled.add(name)
        newly_enabled.append(name)
        return newly_enabled

    def disable(self, name: str) -> tuple[bool, list[str]]:
        """Disable a plugin. Returns (success, list of dependents that block it)."""
        if name not in self._enabled:
            return True, []

        # Check if anything enabled depends on this
        dependents = [
            p.name for p in self._plugins.values()
            if p.name in self._enabled and name in p.depends
        ]
        if dependents:
            return False, dependents

        self._enabled.discard(name)
        return True, []

    def get_runner(self, mode: str) -> Callable | None:
        """Get the run function for a mode, if its plugin is enabled."""
        plugin_name = self._mode_map.get(mode)
        if not plugin_name or plugin_name not in self._enabled:
            return None
        plugin = self._plugins.get(plugin_name)
        if not plugin:
            return None
        return plugin.run

    def resolve_mode(self, mode: str) -> str | None:
        """Map a mode name to its plugin name."""
        return self._mode_map.get(mode)

    def status(self) -> dict:
        """Return full registry status for UI/debugging."""
        return {
            "plugins": {
                name: {
                    "enabled": name in self._enabled,
                    "depends": p.depends,
                    "provides": p.provides,
                    "default": p.default,
                    "description": p.description,
                }
                for name, p in self._plugins.items()
            },
            "modes": dict(self._mode_map),
            "enabled_count": len(self._enabled),
            "total_count": len(self._plugins),
        }
