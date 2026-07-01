"""Thin HTTP client for the local FastAPI control-plane.

The Telegram bot uses this to spawn render / upload / confirm jobs
through the same code path the web UI uses — reusing the JobManager
single-writer locks + ring-buffered SSE tail. Direct subprocess spawn
from the bot would bypass those locks and risk racing.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)

_DEFAULT_BASE = "http://127.0.0.1:8765"
_TIMEOUT_S = 20


class WebappError(RuntimeError):
    pass


class WebappClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("WEBAPP_BASE")
                         or _DEFAULT_BASE).rstrip("/")
        # Host-header allowlist middleware only accepts the loopback
        # names — set an explicit Host so a caller that resolves to
        # 127.0.0.1 via /etc/hosts doesn't get rejected.
        self._session = requests.Session()
        self._session.headers.update({"Host": "127.0.0.1:8765"})

    def _get(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        try:
            r = self._session.get(url, timeout=_TIMEOUT_S)
        except requests.RequestException as e:
            raise WebappError(f"GET {path} network error: {e}") from e
        if r.status_code >= 400:
            raise WebappError(f"GET {path} {r.status_code}")
        try:
            return r.json()
        except ValueError as e:
            raise WebappError(f"GET {path} non-JSON") from e

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            r = self._session.post(url, json=body, timeout=_TIMEOUT_S)
        except requests.RequestException as e:
            raise WebappError(f"POST {path} network error: {e}") from e
        if r.status_code >= 400:
            detail = ""
            try:
                detail = r.json().get("detail", "")
            except Exception:  # noqa: BLE001
                detail = r.text[:200]
            raise WebappError(f"POST {path} {r.status_code}: {detail}")
        try:
            return r.json()
        except ValueError as e:
            raise WebappError(f"POST {path} non-JSON response") from e

    # ---- Jobs ----

    def start_render(self, *, limit: int, dry_run: bool = False,
                     progress_chat_id: int | None = None,
                     progress_message_id: int | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {"limit": limit, "dry_run": dry_run}
        if progress_chat_id is not None:
            body["progress_chat_id"] = progress_chat_id
        if progress_message_id is not None:
            body["progress_message_id"] = progress_message_id
        return self._post("/api/jobs/render", body)

    def start_upload(self, *, visibility: str, force: bool = False,
                     dry_run: bool = False, aigc: bool = True,
                     post_id: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {
            "visibility": visibility,
            "force": force,
            "dry_run": dry_run,
            "aigc": aigc,
        }
        if post_id:
            body["post_id"] = post_id
        return self._post("/api/jobs/upload", body)

    def list_approved(self) -> list[dict[str, Any]]:
        r = self._get("/api/renders/approved")
        return r if isinstance(r, list) else []

    def start_confirm(self, *, force: bool = False) -> dict[str, Any]:
        return self._post("/api/jobs/confirm", {"force": force})

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self._get(f"/api/jobs/{job_id}")

    def wait_for_short_job(self, job_id: str, *, timeout_s: float = 5.0,
                           poll_s: float = 0.4) -> dict[str, Any]:
        """Poll GET /api/jobs/{id} until it ends OR `timeout_s` elapses.
        Returns the final job dict either way. For short-lived workers
        (upload gates closed, confirm scrape, upload dry-run) this hands
        back the outcome; for a real upload that takes minutes the caller
        gets a still-running job and should just tell the user "running…"."""
        import time
        deadline = time.monotonic() + timeout_s
        job = self.get_job(job_id)
        while job.get("running") and time.monotonic() < deadline:
            time.sleep(poll_s)
            job = self.get_job(job_id)
        return job

    def job_tail_text(self, job_id: str, *, timeout_s: float = 2.0,
                      max_bytes: int = 4096) -> str:
        """Consume `/api/jobs/{id}/stream` for up to timeout_s and return
        the concatenated `data:` lines. For finished jobs this replays
        the ring buffer + end frame instantly; for running jobs it drains
        whatever's queued."""
        import time
        url = f"{self.base_url}/api/jobs/{job_id}/stream"
        try:
            r = self._session.get(url, stream=True, timeout=(_TIMEOUT_S, timeout_s + 5))
        except requests.RequestException:
            return ""
        try:
            deadline = time.monotonic() + timeout_s
            lines: list[str] = []
            total = 0
            for raw in r.iter_lines(decode_unicode=True):
                if raw is None:
                    if time.monotonic() >= deadline:
                        break
                    continue
                if raw.startswith("data:"):
                    ln = raw[len("data:"):].lstrip()
                    lines.append(ln)
                    total += len(ln)
                if total >= max_bytes or time.monotonic() >= deadline:
                    break
            return "\n".join(lines)
        finally:
            r.close()
