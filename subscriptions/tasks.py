import asyncio

from celery import shared_task

from subscriptions.services.subscriptions import mark_expired_subscriptions


@shared_task(name="subscriptions.expire_subscriptions", ignore_result=True)
def expire_subscriptions() -> None:
    asyncio.run(mark_expired_subscriptions())
