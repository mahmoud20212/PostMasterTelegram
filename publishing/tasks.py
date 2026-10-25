from celery import shared_task


@shared_task(bind=True, name="publishing.publish_user", ignore_result=True)
def publish_user(self, telegram_user_id: int, run_id: str | None = None) -> None:
    """Execute a specific generation once; legacy messages without an ID are no-ops."""
    import asyncio

    from publishing.services.engine import _run

    asyncio.run(_run(telegram_user_id, run_id))
