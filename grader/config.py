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
    v2: bool = True            # evidence-anchored AI-vs-reference grading (default path)
    # Object storage lives in Cloudflare R2 (S3 API). DB stays on Supabase.
    r2_account_id: str = ""
    r2_access_key: str = ""
    r2_secret: str = ""
    r2_endpoint: str = ""
    r2_bucket_content: str = ""
    r2_bucket_submissions: str = ""

    @property
    def rest_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/rest/v1"

    @property
    def storage_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/storage/v1"

    @property
    def r2_s3_endpoint(self) -> str:
        base = self.r2_endpoint or (
            f"https://{self.r2_account_id}.r2.cloudflarestorage.com" if self.r2_account_id else ""
        )
        if not base:
            raise ConfigError("R2 not configured: set R2_S3_ENDPOINT or R2_ACCOUNT_ID.")
        return base.rstrip("/")

    def r2_bucket_for(self, logical: str) -> str:
        """Map the DB's logical bucket name to the real R2 bucket."""
        mapping = {
            "assignment-content": self.r2_bucket_content,
            "assignment-submissions": self.r2_bucket_submissions,
        }
        b = mapping.get(logical, "")
        if not b:
            raise ConfigError(
                f"No R2 bucket for logical '{logical}' — set R2_BUCKET_CONTENT / R2_BUCKET_SUBMISSIONS."
            )
        return b

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
        v2=_optional("GRADER_V2", "1") not in ("0", "false", "no"),
        r2_account_id=_optional("R2_ACCOUNT_ID"),
        r2_access_key=_optional("R2_ACCESS_KEY_ID"),
        r2_secret=_optional("R2_SECRET_ACCESS_KEY"),
        r2_endpoint=_optional("R2_S3_ENDPOINT"),
        r2_bucket_content=_optional("R2_BUCKET_CONTENT"),
        r2_bucket_submissions=_optional("R2_BUCKET_SUBMISSIONS"),
    )
