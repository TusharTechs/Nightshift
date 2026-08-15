"""Celery application (Sprint 1 Feature 5: Asynchronous Background Job
Framework & Queue Setup).

Queue topology matches Technical Blueprint Section 3.4. Sprint 1 used only
celery:observation and celery:cron; Sprint 2 is the first to register tasks
on celery:reasoning (Product Inspection Engine scan stays on celery:observation;
Morning Shift Report compilation, which aggregates the AI-derived issue data
inspect_catalog just produced, runs on celery:reasoning).
"""

from __future__ import annotations

from datetime import timedelta

from celery import Celery
from kombu import Queue

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "nightshift",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "workers.tasks.discovery",
        "workers.tasks.inspection",
        "workers.tasks.discount_inspection",
        "workers.tasks.theme_inspection",
        "workers.tasks.tracking_inspection",
        "workers.tasks.shift_report",
        "workers.tasks.planning",
        "workers.tasks.execution",
        "workers.tasks.verification",
        "workers.tasks.scheduler",
    ],
)

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_queues=(
        Queue("celery:observation"),
        Queue("celery:reasoning"),
        Queue("celery:execution"),
        Queue("celery:verification"),
        Queue("celery:cron"),
    ),
    task_default_queue="celery:observation",
    task_routes={
        "tasks.store_discovery": {"queue": "celery:observation"},
        "tasks.inspect_catalog": {"queue": "celery:observation"},
        "tasks.inspect_discounts": {"queue": "celery:observation"},
        "tasks.inspect_theme_files": {"queue": "celery:observation"},
        "tasks.inspect_tracking_scripts": {"queue": "celery:observation"},
        "tasks.compile_shift_report": {"queue": "celery:reasoning"},
        "tasks.plan_cognitive_tasks": {"queue": "celery:reasoning"},
        "tasks.execute_cognitive_task": {"queue": "celery:execution"},
        "tasks.verify_execution": {"queue": "celery:verification"},
        "tasks.dispatch_nightly_shifts": {"queue": "celery:cron"},
    },
)

# Nightly scheduler (Sprint 4): closes the gap flagged since Sprint 2's own
# completion report ("no scheduler triggers the pipeline automatically" —
# "Open product decision, not yet made"). Only takes effect for a process
# actually running `celery -A ... beat` (docker-compose.yml's `beat`
# service) — a plain worker process never reads `beat_schedule` on its own.
if settings.shift_schedule_enabled:
    celery_app.conf.beat_schedule = {
        "nightly-shift-dispatch": {
            "task": "tasks.dispatch_nightly_shifts",
            "schedule": timedelta(minutes=settings.shift_schedule_interval_minutes),
        },
    }
