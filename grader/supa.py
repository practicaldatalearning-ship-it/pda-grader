"""Thin Supabase client for the grader — the ONLY DB/storage surface.

DB access is exclusively via the SECURITY-DEFINER `grader_*` RPCs (PostgREST
`/rest/v1/rpc/*`). Storage access is via the authenticated object endpoint using
the same restricted GRADER_KEY (role=grader), gated by RLS to the two assignment
buckets. No service_role key, no signing secret. See db/schema.sql + STRICT §1.3.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from .config import Config

log = logging.getLogger("grader.supa")


class SupaError(RuntimeError):
    pass


class Supa:
    def __init__(self, cfg: Config, session: Optional[requests.Session] = None):
        self.cfg = cfg
        self.s = session or requests.Session()
        # PostgREST needs both apikey and Authorization; both carry the restricted key.
        self._headers = {
            "apikey": cfg.grader_key,
            "Authorization": f"Bearer {cfg.grader_key}",
            "Content-Type": "application/json",
        }

    # ---- RPC ----------------------------------------------------------------
    def _rpc(self, fn: str, params: dict[str, Any]) -> Any:
        url = f"{self.cfg.rest_url}/rpc/{fn}"
        r = self.s.post(url, json=params, headers=self._headers, timeout=30)
        if r.status_code >= 400:
            # Never echo the key; PostgREST error bodies do not contain it.
            raise SupaError(f"rpc {fn} -> {r.status_code}: {r.text[:400]}")
        if not r.content:
            return None
        return r.json()

    def claim_batch(self, limit: int) -> list[dict]:
        return self._rpc("grader_claim_batch", {"p_limit": limit}) or []

    def submission_bundle(self, submission_id: str) -> dict:
        b = self._rpc("grader_submission_bundle", {"p_submission": submission_id})
        if not isinstance(b, dict) or b.get("error"):
            raise SupaError(f"bundle for {submission_id}: {b}")
        return b

    def write_result(
        self, submission_id: str, total: float, per_question: list[dict],
        status: str, error: Optional[str] = None,
    ) -> None:
        self._rpc("grader_write_result", {
            "p_submission": submission_id,
            "p_total": total,
            "p_per_question": per_question,
            "p_status": status,
            "p_error": error,
        })

    def flag_review(self, submission_id: str, question_id: str, reason: str,
                    suggested: Optional[float]) -> None:
        self._rpc("grader_flag_review", {
            "p_submission": submission_id,
            "p_question": question_id,
            "p_reason": reason,
            "p_suggested": suggested,
        })

    def requeue_stuck(self, older_than: str = "1 hour") -> int:
        return int(self._rpc("grader_requeue_stuck", {"p_older_than": older_than}) or 0)

    # ---- Author jobs (G0b) --------------------------------------------------
    def claim_author_jobs(self, limit: int) -> list[dict]:
        return self._rpc("grader_claim_author_jobs", {"p_limit": limit}) or []

    def write_authored(self, assignment_id: str, student_nb_path: Optional[str],
                       expected: dict, status: str = "ready",
                       error: Optional[str] = None) -> None:
        self._rpc("grader_write_authored", {
            "p_assignment": assignment_id,
            "p_student_nb_path": student_nb_path,
            "p_expected": expected,
            "p_status": status,
            "p_error": error,
        })

    # ---- Storage ------------------------------------------------------------
    def download(self, bucket: str, path: str) -> bytes:
        """Download a private object via the authenticated endpoint (role=grader)."""
        url = f"{self.cfg.storage_url}/object/authenticated/{bucket}/{path}"
        r = self.s.get(url, headers={"apikey": self.cfg.grader_key,
                                     "Authorization": f"Bearer {self.cfg.grader_key}"},
                       timeout=60)
        if r.status_code >= 400:
            raise SupaError(f"download {bucket}/{path} -> {r.status_code}")
        return r.content

    def upload(self, bucket: str, path: str, data: bytes,
               content_type: str = "application/octet-stream") -> None:
        """Upsert an object (author job writes the generated student notebook)."""
        url = f"{self.cfg.storage_url}/object/{bucket}/{path}"
        r = self.s.post(url, data=data, headers={
            "apikey": self.cfg.grader_key,
            "Authorization": f"Bearer {self.cfg.grader_key}",
            "Content-Type": content_type,
            "x-upsert": "true",
        }, timeout=60)
        if r.status_code >= 400:
            raise SupaError(f"upload {bucket}/{path} -> {r.status_code}: {r.text[:200]}")
