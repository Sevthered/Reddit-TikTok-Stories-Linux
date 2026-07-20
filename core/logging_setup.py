from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


_FORMAT = "%(asctime)s %(levelname)s %(name)s :: %(message)s"


def setup_logging(log_dir: str | Path = "logs", level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter(_FORMAT)

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setFormatter(fmt)
    root.addHandler(stderr)

    # The rotating file handler writes a pod-local `bot.log` that is invisible
    # to `kubectl logs` and just consumes the PVC in-cluster. Off by default;
    # opt in with LOG_TO_FILE=1 (systemd / local runs) — stderr is always on so
    # `kubectl logs` stays the source of truth ([[ELK-Stack-Logging]]).
    if os.environ.get("LOG_TO_FILE", "").lower() in ("1", "true", "yes"):
        p = Path(log_dir)
        p.mkdir(parents=True, exist_ok=True)
        file_h = RotatingFileHandler(
            p / "bot.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_h.setFormatter(fmt)
        root.addHandler(file_h)
