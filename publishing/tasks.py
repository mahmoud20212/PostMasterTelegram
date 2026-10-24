from celery import shared_task


@shared_task(bind=True, name="publishing.publish_user", ignore_result=True)
def publish_user(self, telegram_user_id: int) -> None:
    """Run one user's publisher in a Celery worker until the DB flag is disabled."""
    import asyncio

    from publishing.services.engine import _run

    asyncio.run(_run(telegram_user_id))
