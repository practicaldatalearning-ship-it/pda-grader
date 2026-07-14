"""Environment-driven config. Fails loudly if a required secret is missing —
NEVER a default literal for a credential (STRICT-INSTRUCTIONS §1.2)."""
from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise ConfigError(
            f"Missing required env var {name!r}. It must be injected at run time "
            f"(GitHub Actions Secrets), never hardcoded. See STRICT-INSTRUCTIONS.md."
        )
    return val


def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Config:
    supabase_url: str          # e.g. https://<ref>.supabase.co
    anon_key: str              # PUBLIC anon/publishable key — sent as the `apikey` header
    grader_key: str            # restricted JWT (role=grader) — sent as `Authorization: Bearer`
    coach_url: str             # pda-coach worker base URL (LLM-judge)
    coach_key: str             # x-coach-key shared secret
    batch: int                 # submissions per run
    docker_image: str          # sandbox image tag
    run_timeout: int           # per-notebook wall-clock cap (seconds)
    grade_author_jobs: bool    # also run G0b author jobs this run

    @property
    def rest_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/rest/v1"

    @property
    def storage_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/storage/v1"

    @property
    def has_coach(self) -> bool:
        return bool(self.coach_url and self.coach_key)


def load_config() -> Config:
    return Config(
        supabase_url=_require("SUPABASE_URL"),
        # PUBLIC key (anon/publishable). Supabase's gateway validates the `apikey`
        # header against it; the grader role itself comes from GRADER_KEY (Bearer).
        anon_key=_require("SUPABASE_ANON_KEY"),
        grader_key=_require("GRADER_KEY"),
        # Coach is optional at load time: written/task questions degrade to
        # 'review' if it is absent, but the batch still runs (G4 graceful).
        coach_url=_optional("COACH_URL"),
        coach_key=_optional("COACH_KEY"),
        batch=int(_optional("GRADER_BATCH", "15") or "15"),
        docker_image=_optional("GRADER_IMAGE", "pda-sandbox"),
        run_timeout=int(_optional("GRADER_RUN_TIMEOUT", "300") or "300"),
        grade_author_jobs=_optional("GRADER_AUTHOR_JOBS", "1") not in ("0", "false", "no"),
    )
