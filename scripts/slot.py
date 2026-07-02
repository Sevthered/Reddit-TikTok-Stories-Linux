#!/usr/bin/env python3
"""Fixed-schedule slot orchestrator: 4 slots/day at 00:00, 06:00, 12:00, 18:00
Europe/Madrid.

Two systemd-invoked subcommands:
  render --instance HHMM   fires 30 min before the publish slot; runs the
                            pipeline once, stores the resulting post_id in
                            data/slots/HH.json, and for auto slots (00, 06)
                            immediately flips the row from `pending` to
                            `approved` so the upload subcommand can consume
                            it without human intervention. For manual slots
                            (12, 18) the Telegram review card fires per
                            main.py's normal flow, and the operator has
                            ~22 min to Approve/Reject.
  upload --instance HHMM    fires AT the publish hour; reads the manifest,
                            resolves the row state, and uploads via
                            pipeline.upload_worker with --post-id. If the
                            row is `rejected`, the slot is skipped. If it is
                            still `pending` at slot time (manual slot, no
                            reply), the row is force-approved and published
                            anyway.

Slot config lives in SLOTS below, keyed by the systemd instance name.
Every timing decision is made in this module so the systemd units are
purely mechanical.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.config import _load_dotenv  # noqa: E402
from core.db import Db, UPLOAD_APPROVED, UPLOAD_PENDING, UPLOAD_REJECTED  # noqa: E402
from core.logging_setup import setup_logging  # noqa: E402
from core.notify import Notifier, NotifierError, _html_escape  # noqa: E402


log = logging.getLogger("slot")


@dataclass(frozen=True)
class SlotCfg:
    publish_hour: int      # 0..23
    auto_approve: bool     # True → skip Telegram review card
    warn_pre_render: bool  # True → DM "rendering for HH:00 slot" as we start


SLOTS: dict[str, SlotCfg] = {
    "0000": SlotCfg(publish_hour=0,  auto_approve=True,  warn_pre_render=False),
    "0600": SlotCfg(publish_hour=6,  auto_approve=True,  warn_pre_render=False),
    "1200": SlotCfg(publish_hour=12, auto_approve=False, warn_pre_render=True),
    "1800": SlotCfg(publish_hour=18, auto_approve=False, warn_pre_render=True),
}


_MANIFEST_DIR = Path("data/slots")
_RENDER_MAX_ATTEMPTS = 3
_RENDER_RETRY_SLEEP_S = 90


def _notifier() -> Notifier | None:
    try:
        return Notifier.from_env()
    except NotifierError:
        return None


def _send(notifier: Notifier | None, text: str) -> None:
    if notifier is None:
        return
    try:
        notifier.send_text(text, parse_mode="HTML")
    except NotifierError as exc:
        log.warning("telegram send failed: %s", exc)


def _manifest_path(instance: str) -> Path:
    return _MANIFEST_DIR / f"{instance}.json"


def _write_manifest(instance: str, payload: dict) -> None:
    _MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    p = _manifest_path(instance)
    p.write_text(json.dumps(payload, indent=2))


def _read_manifest(instance: str) -> dict | None:
    p = _manifest_path(instance)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def _clear_manifest(instance: str) -> None:
    p = _manifest_path(instance)
    if p.exists():
        p.unlink()


def _latest_pending_post_id(db: Db) -> str | None:
    """Return the most recently created pending render row (the one main.py
    just finished producing). None if none exists."""
    cur = db._conn.execute(  # noqa: SLF001
        "SELECT post_id FROM used WHERE upload_status = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (UPLOAD_PENDING,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _run_pipeline() -> int:
    """Invoke main.py --limit 1 and return its exit code."""
    cmd = [sys.executable, "-u", "main.py", "--limit", "1"]
    log.info("running %s", cmd)
    return subprocess.run(cmd, check=False).returncode


def _cmd_render(instance: str) -> int:
    cfg = SLOTS[instance]
    notifier = _notifier()

    if cfg.warn_pre_render:
        _send(
            notifier,
            f"🎬 <b>Rendering for {cfg.publish_hour:02d}:00 slot</b>\n"
            f"Approval card follows in ~7 minutes. Publish at "
            f"{cfg.publish_hour:02d}:00 Europe/Madrid regardless of approval.",
        )

    last_pending_before: str | None
    with Db.open() as db:
        last_pending_before = _latest_pending_post_id(db)

    exit_code = 1
    for attempt in range(1, _RENDER_MAX_ATTEMPTS + 1):
        log.info("slot %s render attempt %d/%d", instance, attempt, _RENDER_MAX_ATTEMPTS)
        exit_code = _run_pipeline()
        if exit_code == 0:
            break
        log.warning("main.py exit=%s (attempt %d/%d)", exit_code, attempt, _RENDER_MAX_ATTEMPTS)
        if attempt < _RENDER_MAX_ATTEMPTS:
            time.sleep(_RENDER_RETRY_SLEEP_S)

    if exit_code != 0:
        _send(
            notifier,
            f"❌ <b>Render failed for {cfg.publish_hour:02d}:00 slot</b>\n"
            f"main.py exit={exit_code} after {_RENDER_MAX_ATTEMPTS} attempts.\n"
            f"Slot will be skipped.",
        )
        return exit_code

    with Db.open() as db:
        post_id = _latest_pending_post_id(db)
        if post_id is None or post_id == last_pending_before:
            _send(
                notifier,
                f"⚠️ <b>Render produced no new row for {cfg.publish_hour:02d}:00 slot</b>\n"
                f"(main.py exit 0 but no fresh pending post_id found — likely all "
                f"candidates got filtered / skipped). Slot will be skipped.",
            )
            return 2

        _write_manifest(instance, {
            "post_id": post_id,
            "publish_hour": cfg.publish_hour,
            "auto_approve": cfg.auto_approve,
            "rendered_at_epoch": int(time.time()),
        })
        log.info("slot %s rendered post_id=%s", instance, post_id)

        if cfg.auto_approve:
            approved = db.approve(post_id)
            log.info("auto-approved %s (transitioned=%s)", post_id, approved)

    return 0


def _cmd_upload(instance: str) -> int:
    cfg = SLOTS[instance]
    notifier = _notifier()

    manifest = _read_manifest(instance)
    if manifest is None:
        _send(
            notifier,
            f"⏭️ <b>{cfg.publish_hour:02d}:00 slot skipped</b>\n"
            f"No manifest at data/slots/{instance}.json — render likely failed "
            f"earlier. Check journal.",
        )
        return 0

    post_id = manifest["post_id"]

    with Db.open() as db:
        row = db.get_render(post_id)
        if row is None:
            _send(
                notifier,
                f"⚠️ <b>{cfg.publish_hour:02d}:00 slot aborted</b>\n"
                f"post_id={post_id} missing from DB.",
            )
            _clear_manifest(instance)
            return 1

        status = row.upload_status
        log.info("slot %s upload: post_id=%s status=%s", instance, post_id, status)

        if status == UPLOAD_REJECTED:
            _send(
                notifier,
                f"⏭️ <b>{cfg.publish_hour:02d}:00 slot skipped</b>\n"
                f"{post_id} was rejected during the approval window.",
            )
            _clear_manifest(instance)
            return 0

        if status == UPLOAD_PENDING:
            log.info("no approval received; force-approving %s", post_id)
            _send(
                notifier,
                f"⏰ <b>Publishing {cfg.publish_hour:02d}:00 slot without approval</b>\n"
                f"No reply received. Force-approving {post_id} and uploading now.",
            )
            db.approve(post_id)
        elif status != UPLOAD_APPROVED:
            _send(
                notifier,
                f"⚠️ <b>{cfg.publish_hour:02d}:00 slot aborted</b>\n"
                f"{post_id} is in status={status!r}; expected approved / pending / rejected.",
            )
            _clear_manifest(instance)
            return 1

    cmd = [sys.executable, "-u", "pipeline/upload_worker.py", "--post-id", post_id]
    log.info("running %s", cmd)
    rc = subprocess.run(cmd, check=False).returncode
    if rc != 0:
        _send(
            notifier,
            f"❌ <b>{cfg.publish_hour:02d}:00 slot upload FAILED</b>\n"
            f"post_id=<code>{_html_escape(post_id)}</code> upload_worker exit={rc}.",
        )
    _clear_manifest(instance)
    return rc


def main(argv: list[str] | None = None) -> int:
    _load_dotenv()
    setup_logging()

    p = argparse.ArgumentParser(description="Slot orchestrator (fixed-schedule renders + uploads).")
    sub = p.add_subparsers(dest="action", required=True)

    p_r = sub.add_parser("render", help="render for a slot; auto-approve for auto slots.")
    p_r.add_argument("--instance", required=True, choices=sorted(SLOTS))

    p_u = sub.add_parser("upload", help="upload a slot's rendered post at publish time.")
    p_u.add_argument("--instance", required=True, choices=sorted(SLOTS))

    args = p.parse_args(argv)
    if args.action == "render":
        return _cmd_render(args.instance)
    if args.action == "upload":
        return _cmd_upload(args.instance)
    return 2


if __name__ == "__main__":
    sys.exit(main())
