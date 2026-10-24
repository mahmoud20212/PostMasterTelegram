from asgiref.sync import sync_to_async

from messages.models import SavedMessage
from subscriptions.services.subscriptions import _check_limit
from users.models import User


@sync_to_async
def create_message(*, telegram_user_id: int, text: str, entities: list[dict] | None = None) -> SavedMessage:
    check_limit_sync(telegram_user_id=telegram_user_id, resource="messages")
    user = User.objects.get(telegram_user_id=telegram_user_id)
    stored_text = text if entities else text.strip()
    return SavedMessage.objects.create(user=user, text=stored_text, entities=entities or [])


def check_limit_sync(*, telegram_user_id: int, resource: str) -> None:
    _check_limit(telegram_user_id=telegram_user_id, resource=resource)


@sync_to_async
def list_messages(*, telegram_user_id: int) -> list[SavedMessage]:
    return list(SavedMessage.objects.filter(user__telegram_user_id=telegram_user_id))


@sync_to_async
def toggle_message(*, telegram_user_id: int, message_id: int) -> SavedMessage:
    message = SavedMessage.objects.get(id=message_id, user__telegram_user_id=telegram_user_id)
    message.is_active = not message.is_active
    message.save(update_fields=["is_active", "updated_at"])
    return message


@sync_to_async
def get_message(*, telegram_user_id: int, message_id: int) -> SavedMessage:
    return SavedMessage.objects.get(id=message_id, user__telegram_user_id=telegram_user_id)


@sync_to_async
def delete_message(*, telegram_user_id: int, message_id: int) -> None:
    SavedMessage.objects.filter(id=message_id, user__telegram_user_id=telegram_user_id).delete()
