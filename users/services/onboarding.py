from asgiref.sync import sync_to_async

from users.models import User


@sync_to_async
def register_telegram_user(*, telegram_user_id: int, username: str = "", first_name: str = "") -> User:
    user, _ = User.objects.update_or_create(
        telegram_user_id=telegram_user_id,
        defaults={"username": username, "first_name": first_name, "status": "active"},
    )
    return user


@sync_to_async
def get_telegram_user(*, telegram_user_id: int) -> User:
    return User.objects.get(telegram_user_id=telegram_user_id)
