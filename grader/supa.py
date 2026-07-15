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
        # Supabase's gateway validates `apikey` (must be the anon/publishable key);
        # PostgREST derives the role from the `Authorization: Bearer` JWT (grader).
        self._auth = {
            "apikey": cfg.anon_key,
            "Authorization": f"Bearer {cfg.grader_key}",
        }
        self._headers = {**self._auth, "Content-Type": "application/json"}
        self._s3 = None  # lazy boto3 R2 client (object storage)

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

    # ---- Storage (Cloudflare R2, S3 API) ------------------------------------
    def _r2(self):
        """Lazily build the boto3 R2 client. Path-style + s3v4 for a custom endpoint."""
        if self._s3 is None:
            import boto3
            from botocore.config import Config as BotoConfig
            self._s3 = boto3.client(
                "s3",
                endpoint_url=self.cfg.r2_s3_endpoint,
                aws_access_key_id=self.cfg.r2_access_key,
                aws_secret_access_key=self.cfg.r2_secret,
                region_name="auto",
                config=BotoConfig(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )
        return self._s3

    def download(self, bucket: str, path: str) -> bytes:
        """Download a private object from its R2 bucket (bucket = logical name)."""
        try:
            obj = self._r2().get_object(Bucket=self.cfg.r2_bucket_for(bucket), Key=path)
            return obj["Body"].read()
        except Exception as e:  # boto3 ClientError etc.
            raise SupaError(f"download {bucket}/{path} -> {e}")

    def upload(self, bucket: str, path: str, data: bytes,
               content_type: str = "application/octet-stream") -> None:
        """Upsert an object (author job writes the generated student notebook)."""
        try:
            self._r2().put_object(
                Bucket=self.cfg.r2_bucket_for(bucket), Key=path,
                Body=data, ContentType=content_type,
            )
        except Exception as e:
            raise SupaError(f"upload {bucket}/{path} -> {e}")
