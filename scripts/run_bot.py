"""Long-lived Telegram callback bot runner.

Invoked by the `com.sebastian.tiktok-bot` LaunchAgent. Loops forever
polling `getUpdates` and dispatching Approve/Reject taps + slash
commands. On any uncaught crash, launchd's KeepAlive restarts us.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# Allow `python scripts/run_bot.py` (launchd invokes us that way).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.config import _load_dotenv
from core.db import Db
from core.logging_setup import setup_logging
from core.notify import Notifier, run_callback_bot


def main() -> int:
    _load_dotenv()
    setup_logging()
    log = logging.getLogger("run_bot")

    try:
        notifier = Notifier.from_env()
    except Exception as e:
        log.error("cannot start bot: %s", e)
        return 2

    log.info("telegram bot up — chat_id=%d", notifier.chat_id)
    with Db.open() as db:
        run_callback_bot(db, notifier=notifier)  # blocks forever
    return 0


if __name__ == "__main__":
    sys.exit(main())
