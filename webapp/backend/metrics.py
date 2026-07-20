"""Prometheus instrumentation for the webapp (task #32).

Two layers:
  1. HTTP RED metrics via prometheus-fastapi-instrumentator (request rate/errors/
     latency, path-TEMPLATED so `/api/renders/{id}` never explodes cardinality).
  2. DB-derived business gauges via a custom collector that queries the shared
     SQLite DB on each scrape — this is how we observe the SHORT-LIVED pipeline
     CronJobs (render/upload/confirm/retention) without a Pushgateway: their
     durable outcomes live in the DB, and the always-on webapp exposes them.

Metrics are served on a SEPARATE port (settings.METRICS_PORT, default 9100) via
prometheus_client's own HTTP server, NOT on the main app port — so they are never
routed by the ingress/cloudflared and stay ClusterIP-only. CronJob liveness
(success/fail/duration/last-schedule) is NOT emitted here; it comes for free from
kube-state-metrics.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from prometheus_client import REGISTRY, Counter, start_http_server
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from core.db import Db
from webapp.backend import settings

log = logging.getLogger("webapp.metrics")

_started = False

# Auth/CSRF rejections by cause. Incremented from app.py's security
# middlewares. Distinguishes rejection reason (host / cf_access_missing /
# cf_access_invalid / csrf / origin) which the RED status-code counter can't.
# Lives on the default REGISTRY (served on :METRICS_PORT); increments are cheap
# no-ops when the metrics server isn't started.
AUTH_REJECTED = Counter(
    "tiktok_auth_rejected_total",
    "Requests rejected by the webapp auth/CSRF middlewares, by reason",
    ["reason"],
)


def instrument(app) -> None:
    """Attach the HTTP RED middleware. Call at import time (before startup) so the
    middleware is registered; does NOT expose /metrics on the app port."""
    from prometheus_fastapi_instrumentator import Instrumentator

    Instrumentator(
        should_group_status_codes=True,      # 2xx/3xx/... instead of every code
        should_ignore_untemplated=True,      # drop unmatched paths (scanner noise)
        excluded_handlers=["/metrics"],
    ).instrument(app, metric_namespace="tiktok", metric_subsystem="api")


def _iso_to_epoch(s: str | None) -> float | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:  # noqa: BLE001
        return None


class TikTokDBCollector(Collector):
    """Computes business gauges from the SQLite DB on every scrape. Read-only
    queries; Db.open's schema init is idempotent (no-op on the live DB)."""

    def collect(self):
        try:
            with Db.open(settings.DB_PATH) as db:
                by_status = GaugeMetricFamily(
                    "tiktok_posts_by_status",
                    "Rows in `used` grouped by upload_status",
                    labels=["status"],
                )
                total = 0
                for s, n in db._conn.execute(
                    "SELECT COALESCE(upload_status,'none') AS s, COUNT(*) "
                    "FROM used GROUP BY s"
                ).fetchall():
                    by_status.add_metric([str(s)], n)
                    total += n
                yield by_status

                yield GaugeMetricFamily(
                    "tiktok_used_stories_total",
                    "Total rows in the used/dedup corpus", value=total)
                yield GaugeMetricFamily(
                    "tiktok_posts_today",
                    "Posts uploaded today (local tz)",
                    value=db.posts_today(settings.madrid_tz_offset_hours()))
                yield GaugeMetricFamily(
                    "tiktok_pending_review_count",
                    "Renders pending review", value=len(db.pending_renders()))
                yield GaugeMetricFamily(
                    "tiktok_approved_ready_count",
                    "Approved renders ready to upload", value=len(db.approved_ready()))
                yield GaugeMetricFamily(
                    "tiktok_under_review_count",
                    "Posts under review", value=len(db.under_review()))
                yield GaugeMetricFamily(
                    "tiktok_uploads_enabled",
                    "Kill switch: 1 if uploads are enabled",
                    value=1 if db.is_uploads_enabled() else 0)
                yield GaugeMetricFamily(
                    "tiktok_slots_configured",
                    "Number of configured schedule slots", value=len(db.list_slots()))

                ts = _iso_to_epoch(db.last_uploaded_at())
                if ts is not None:
                    yield GaugeMetricFamily(
                        "tiktok_last_upload_timestamp_seconds",
                        "Unix time of the most recent upload "
                        "(alert on time() - this)", value=ts)
        except Exception as e:  # noqa: BLE001
            log.warning("metrics: DB collect failed: %s", e)
            yield GaugeMetricFamily(
                "tiktok_metrics_collect_failed",
                "1 if the most recent DB metrics scrape errored", value=1)
            return

        # Session-cookie freshness — reads the cookies file, not the DB.
        try:
            from pipeline.upload import sessionid_expires_in_days
            days = sessionid_expires_in_days(settings.COOKIES_PATH)
            if days is not None:
                yield GaugeMetricFamily(
                    "tiktok_sessionid_days_remaining",
                    "Days until the TikTok session cookie expires", value=days)
        except Exception as e:  # noqa: BLE001
            log.debug("metrics: sessionid probe failed: %s", e)


def start_metrics_server() -> None:
    """Register the DB collector and start the :METRICS_PORT metrics HTTP server.
    Idempotent (guards against double-start on reload)."""
    global _started
    if _started:
        return
    REGISTRY.register(TikTokDBCollector())
    start_http_server(settings.METRICS_PORT)
    _started = True
    log.info("metrics: serving on :%d", settings.METRICS_PORT)
