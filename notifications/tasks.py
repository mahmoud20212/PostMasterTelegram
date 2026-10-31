from celery import shared_task


@shared_task(name="notifications.deliver", ignore_result=True, acks_late=True,
             reject_on_worker_lost=True, soft_time_limit=18, time_limit=22)
def deliver(event_id, token):
    from notifications.delivery import deliver_notification
    deliver_notification(event_id, token)
