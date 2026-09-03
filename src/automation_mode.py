"""Fail-closed runtime mode for unattended automation."""
from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class AutomationMode:
    algo_env: str
    auto_upload: bool
    dry_run: bool

    @property
    def live_publish(self) -> bool:
        return self.auto_upload and not self.dry_run


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be an explicit boolean value")


def resolve_automation_mode() -> AutomationMode:
    """Resolve scheduled automation without ever defaulting to a live publish."""
    mode = AutomationMode(
        algo_env=os.getenv("ALGO_ENV", "").strip().lower(),
        auto_upload=_env_flag("AGENT_AUTO_UPLOAD", default=False),
        dry_run=_env_flag("AGENT_DRY_RUN", default=True),
    )
    if mode.live_publish and mode.algo_env != "production":
        raise RuntimeError(
            "live automation requires ALGO_ENV=production, "
            "AGENT_AUTO_UPLOAD=true, and AGENT_DRY_RUN=false"
        )
    return mode
