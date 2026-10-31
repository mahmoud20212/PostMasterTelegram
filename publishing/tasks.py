from celery import shared_task


@shared_task(bind=True, name="publishing.publish_user", ignore_result=True)
def publish_user(self, telegram_user_id: int, run_id: str | None = None) -> None:
    """Retired message type. Old queued messages must never start the new engine."""
    return None


@shared_task(name="publishing.publish_step", ignore_result=True, acks_late=True,
             reject_on_worker_lost=True)
def publish_step(run_id: str, dispatch_token: str) -> None:
    from publishing.services.engine import execute_step

    execute_step(run_id, dispatch_token)


@shared_task(name="publishing.dispatch_due", ignore_result=True, expires=5)
def dispatch_due_task() -> None:
    from publishing.services.scheduler import dispatch_due
    from notifications.delivery import dispatch_notifications

    try:
        dispatch_due()
    finally:
        dispatch_notifications()
