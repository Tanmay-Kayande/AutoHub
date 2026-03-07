"""
    Registers the pipeline job and starts the scheduler.
    Called once at FastAPI startup.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from autohub.automation.pipeline import run_full_pipeline

scheduler = BackgroundScheduler()

def start_scheduler():

    scheduler.add_job(
        func=run_full_pipeline,
        trigger=CronTrigger(day =1, hour=0, minute=0),  # Run on the 1st of every month at midnight
        id="auto_brochure_pipeline",
        name="AutoHub Brochure Pipeline",
        replace_existing=True,
    )

    scheduler.start()
    print("[Scheduler] AutoHub pipeline scheduled — runs on 1st of every month at 12:00 AM (midnight)")

def stop_scheduler():
    """
    Gracefully shuts down the scheduler.
    Called at FastAPI shutdown so no jobs are left hanging.
    """
    if scheduler.running:
        scheduler.shutdown()
        print("[Scheduler] Scheduler stopped.")