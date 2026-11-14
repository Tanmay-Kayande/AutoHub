"""In-process scheduler for the monthly pipeline run (APScheduler).

Several API workers may each start a scheduler; that is safe because the
pipeline's advisory lock lets only one run proceed and records the others as
``skipped``.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from autohub.core.settings import get_settings

logger = logging.getLogger(__name__)

JOB_ID = "monthly_pipeline"
_scheduler: BackgroundScheduler | None = None


def _run_scheduled() -> None:
    from autohub.jobs.pipeline import PipelineOptions, run_pipeline

    try:
        run_pipeline(PipelineOptions(trigger="schedule"))
    except Exception:  # the run row already records the failure
        logger.exception("scheduled_pipeline_failed")


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler
    settings = get_settings()
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        _run_scheduled,
        CronTrigger.from_crontab(settings.pipeline_cron, timezone="UTC"),
        id=JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info("scheduler_started cron=%r", settings.pipeline_cron)
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")
    _scheduler = None
