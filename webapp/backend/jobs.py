"""Background job runner for pipeline triggers.

Each `kind` (render / upload / confirm) has a single-writer `asyncio.Lock`
so the web UI can't stack two renders that both write to the SQLite row
for the same post_id. Output lines are captured into a bounded ring
buffer *and* fanned out to per-subscriber queues so `/stream` endpoints
get every line in order — old backlog first, then live tail.

Subprocess is spawned with `python -u` + `PYTHONUNBUFFERED=1` so ffmpeg /
mlx-whisper progress lines flush without a 4 KiB pipe wait. stderr is
merged into stdout to preserve interleaving (order matters when tracing
a render).
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

log = logging.getLogger("webapp.jobs")

Kind = Literal["render", "upload", "confirm"]

# Argv suffix per kind — prepended to whatever the caller passes.
_KIND_ARGV: dict[Kind, list[str]] = {
    "render": ["main.py"],
    "upload": ["-m", "pipeline.upload_worker"],
    "confirm": ["-m", "pipeline.confirm_live"],
}

_LINE_BUFFER = 5000  # ring-buffer cap per job
_CANCEL_GRACE_S = 5.0  # SIGTERM → SIGKILL escalation


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# Best-effort mapping from a log-line fragment to the pipeline stage that
# was running when the error hit. Scanned bottom-up against the tail so
# the *last* recognised marker wins.
_STAGE_MARKERS: list[tuple[str, str]] = [
    ("pipeline.scrape",       "scrape"),
    ("pipeline.filter",       "filter"),
    ("pipeline.tts",          "tts"),
    ("pipeline.background",   "background"),
    ("pipeline.transcribe",   "transcribe"),
    ("faster-whisper",        "transcribe"),
    ("mlx-whisper",           "transcribe"),
    ("pipeline.captions",     "captions"),
    ("pipeline.card",         "card_overlay"),
    ("pipeline.assemble",     "assemble"),
    ("pipeline.cover",        "cover"),
    ("pipeline.review_gate",  "review_gate"),
    ("pipeline.upload",       "upload"),
    ("pipeline.confirm_live", "confirm_live"),
]


def _detect_stage(lines: list[str]) -> str:
    for line in reversed(lines):
        for marker, stage in _STAGE_MARKERS:
            if marker in line:
                return stage
    return "unknown"


def _notify_job_failure(job: "Job") -> None:
    """Send a Telegram DM summarising a failed pipeline job.

    Message includes the job kind, exit code, detected pipeline stage, and
    the last ~20 log lines from the ring buffer. Uses the same
    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID as the bot. Best-effort: any
    error is swallowed by the caller.
    """
    import html
    import urllib.parse
    import urllib.request

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.warning("job failure notify skipped — TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set")
        return

    tail_lines = list(job.lines)[-20:]
    stage = _detect_stage(list(job.lines))
    tail_text = html.escape("\n".join(tail_lines)) or "(no output captured)"

    header = (
        f"❌ <b>{html.escape(job.kind)}</b> failed "
        f"(exit={job.exit_code}, stage=<code>{stage}</code>, "
        f"id=<code>{job.id}</code>)"
    )
    text = f"{header}\n\n<pre>{tail_text[-3500:]}</pre>"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    urllib.request.urlopen(url, data=data, timeout=10)  # noqa: S310 — trusted URL


@dataclass
class Job:
    id: str
    kind: Kind
    args: list[str]
    started_at: str
    ended_at: str | None = None
    exit_code: int | None = None
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=_LINE_BUFFER))
    subscribers: list[asyncio.Queue[str | None]] = field(default_factory=list)
    proc: asyncio.subprocess.Process | None = None

    @property
    def running(self) -> bool:
        return self.ended_at is None


class JobBusyError(RuntimeError):
    """Raised when a same-kind job is already running (single-writer lock)."""


class JobManager:
    def __init__(self, python: Path, repo_root: Path) -> None:
        self._python = python
        self._repo_root = repo_root
        self._jobs: dict[str, Job] = {}
        self._locks: dict[Kind, asyncio.Lock] = {k: asyncio.Lock() for k in _KIND_ARGV}

    def list(self) -> list[Job]:
        return list(self._jobs.values())

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    async def start(self, kind: Kind, args: list[str]) -> Job:
        lock = self._locks[kind]
        if lock.locked():
            raise JobBusyError(f"a {kind!r} job is already running")
        await lock.acquire()
        try:
            argv = [str(self._python), "-u", *_KIND_ARGV[kind], *args]
            # Chrome-for-Testing's crashpad handler resolves its --database
            # path under $HOME; under this unit's ProtectHome=yes, $HOME is
            # inaccessible and the handshake breaks with SIGTRAP (same bug
            # as the systemd upload units, see
            # wiki/bugs/2026-07-03-upload-protecthome-crashpad-crash.md).
            # Jobs here inherit tiktok-webapp.service's environment, so
            # they need the same redirect, independently of the systemd
            # fix on the upload-specific units.
            #
            # DISPLAY is the other half of that same headed-Chromium
            # requirement: the upload job (pipeline.upload_worker) launches
            # Chromium headed and needs an X server. The systemd
            # tiktok-slot-upload@ units set DISPLAY=:99 (Xvfb); the webapp
            # unit doesn't, so a bot/web-triggered upload died with "Missing
            # X server or $DISPLAY". Inject it here so JobManager uploads run
            # under the same Xvfb :99. Inert for the headless kinds
            # (render's cover, confirm's Display-API call).
            chromium_home = self._repo_root / ".chromium-home"
            chromium_home.mkdir(exist_ok=True)
            env = {**os.environ, "PYTHONUNBUFFERED": "1",
                   "HOME": str(chromium_home), "DISPLAY": ":99"}
            log.info("job start kind=%s argv=%s", kind, argv)
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(self._repo_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env,
            )
        except BaseException:
            lock.release()
            raise
        job = Job(
            id=uuid.uuid4().hex[:12],
            kind=kind,
            args=args,
            started_at=_utc_iso(),
            proc=proc,
        )
        self._jobs[job.id] = job
        asyncio.create_task(self._pump(job, lock))
        return job

    async def _pump(self, job: Job, lock: asyncio.Lock) -> None:
        assert job.proc is not None and job.proc.stdout is not None
        try:
            async for raw in job.proc.stdout:
                line = raw.decode("utf-8", "replace").rstrip("\n").rstrip("\r")
                job.lines.append(line)
                for q in list(job.subscribers):
                    try:
                        q.put_nowait(line)
                    except asyncio.QueueFull:
                        # Slow subscriber — drop this event rather than
                        # backpressure the whole pipe.
                        pass
            await job.proc.wait()
            job.exit_code = job.proc.returncode
        except Exception:  # noqa: BLE001
            log.exception("job %s pump crashed", job.id)
            if job.exit_code is None:
                job.exit_code = -1
        finally:
            job.ended_at = _utc_iso()
            log.info("job end   kind=%s id=%s exit=%s", job.kind, job.id, job.exit_code)
            # Send sentinel to every subscriber so they finish cleanly.
            for q in list(job.subscribers):
                try:
                    q.put_nowait(None)
                except asyncio.QueueFull:
                    pass
            # Fire Telegram DM on non-zero exit so failures aren't silent.
            if job.exit_code not in (0, None):
                try:
                    _notify_job_failure(job)
                except Exception:  # noqa: BLE001 — notifier must never mask job outcome
                    log.exception("job %s failure notify raised", job.id)
            lock.release()

    def subscribe(self, job: Job) -> asyncio.Queue[str | None]:
        """Return a queue seeded with the current backlog, then updated
        live as new lines arrive. Terminator `None` is enqueued when the
        job ends. Caller must call `unsubscribe` when done."""
        q: asyncio.Queue[str | None] = asyncio.Queue(maxsize=_LINE_BUFFER * 2)
        for line in list(job.lines):
            q.put_nowait(line)
        if job.running:
            job.subscribers.append(q)
        else:
            q.put_nowait(None)
        return q

    def unsubscribe(self, job: Job, q: asyncio.Queue[str | None]) -> None:
        try:
            job.subscribers.remove(q)
        except ValueError:
            pass

    async def cancel(self, job_id: str) -> bool:
        j = self._jobs.get(job_id)
        if j is None or j.proc is None or not j.running:
            return False
        try:
            j.proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            return False
        try:
            await asyncio.wait_for(j.proc.wait(), timeout=_CANCEL_GRACE_S)
        except asyncio.TimeoutError:
            log.warning("job %s did not exit after SIGTERM — SIGKILL", job_id)
            try:
                j.proc.kill()
            except ProcessLookupError:
                pass
        return True
