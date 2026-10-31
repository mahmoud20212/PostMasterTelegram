from asgiref.sync import sync_to_async

from telegram_accounts.models import TelegramAccount
from users.models import User
from django.utils import timezone
from publishing.services.contention import bounded_transaction, retry_transition

_pending_clients = {}


def _client_config():
    import os
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise ValueError("بيانات Telegram API غير مكتملة في ملف البيئة.")
    return TelegramClient(StringSession(), int(api_id), api_hash)


async def start_login(*, telegram_user_id: int, phone_number: str):
    client = _client_config()
    await client.connect()
    result = await client.send_code_request(phone_number)
    _pending_clients[telegram_user_id] = (client, phone_number, result.phone_code_hash)


async def complete_login(*, telegram_user_id: int, code: str):
    from telethon.errors import SessionPasswordNeededError

    client, phone_number, phone_code_hash = _pending_clients[telegram_user_id]
    try:
        await client.sign_in(phone_number, code, phone_code_hash=phone_code_hash)
    except SessionPasswordNeededError:
        return False
    await _store_connected_session(telegram_user_id=telegram_user_id, client=client)
    return True


async def complete_password_login(*, telegram_user_id: int, password: str):
    client, _, _ = _pending_clients[telegram_user_id]
    await client.sign_in(password=password)
    await _store_connected_session(telegram_user_id=telegram_user_id, client=client)


async def _store_connected_session(*, telegram_user_id: int, client) -> None:
    import os
    from cryptography.fernet import Fernet
    from asgiref.sync import sync_to_async

    key = os.getenv("TELEGRAM_SESSION_ENCRYPTION_KEY")
    if not key:
        raise ValueError("TELEGRAM_SESSION_ENCRYPTION_KEY غير موجود.")
    encrypted = Fernet(key.encode()).encrypt(client.session.save().encode()).decode()
    me = await client.get_me()
    await save_connected_account(
        telegram_user_id=telegram_user_id,
        telegram_account_id=me.id,
        username=me.username or "",
        encrypted_session=encrypted,
    )
    await client.disconnect()
    _pending_clients.pop(telegram_user_id, None)


@sync_to_async
@retry_transition
def save_connected_account(*, telegram_user_id: int, telegram_account_id: int, username: str, encrypted_session: str):
    from publishing.services.runs import _lock_user
    with bounded_transaction():
        user = _lock_user(telegram_user_id)
        account = TelegramAccount.objects.filter(user=user, status="pending").order_by("-id").first()
        if account is None:
            raise ValueError("لا يوجد حساب بانتظار إكمال الربط.")
        account.telegram_user_id = telegram_account_id
        account.username = username
        account.encrypted_session = encrypted_session
        account.status = "connected"
        account.last_connected_at = timezone.now()
        account.is_publishing_account = not TelegramAccount.objects.filter(user=user, is_publishing_account=True).exists()
        account.save(update_fields=["telegram_user_id", "username", "encrypted_session", "status", "last_connected_at", "is_publishing_account"])


@sync_to_async
def list_accounts(*, telegram_user_id: int) -> list[TelegramAccount]:
    return list(TelegramAccount.objects.filter(user__telegram_user_id=telegram_user_id))


@sync_to_async
@retry_transition
def select_publishing_account(*, telegram_user_id: int, account_id: int) -> TelegramAccount:
    from publishing.services.runs import _lock_user
    with bounded_transaction():
        user = _lock_user(telegram_user_id)
        account = TelegramAccount.objects.get(id=account_id, user=user, status="connected")
        TelegramAccount.objects.filter(user=user, is_publishing_account=True).update(is_publishing_account=False)
        account.is_publishing_account = True
        account.save(update_fields=["is_publishing_account"])
        return account


@sync_to_async
def create_pending_account(*, telegram_user_id: int, phone_number: str) -> TelegramAccount:
    from subscriptions.services.subscriptions import check_limit_sync

    check_limit_sync(telegram_user_id=telegram_user_id, resource="accounts")
    user = User.objects.get(telegram_user_id=telegram_user_id)
    phone_number = phone_number.strip().replace(" ", "")
    if TelegramAccount.objects.filter(phone_number=phone_number).exists():
        raise ValueError("هذا الرقم مسجل مسبقاً ولا يمكن تسجيله مرة أخرى.")
    return TelegramAccount.objects.create(user=user, phone_number=phone_number, status="pending")


@sync_to_async
def has_connected_account(*, telegram_user_id: int) -> bool:
    return TelegramAccount.objects.filter(user__telegram_user_id=telegram_user_id, status="connected").exists()


@sync_to_async
def get_account_for_user(*, telegram_user_id: int, account_id: int) -> TelegramAccount | None:
    return TelegramAccount.objects.filter(id=account_id, user__telegram_user_id=telegram_user_id).first()


@sync_to_async
def delete_account_record(*, account_id: int, telegram_user_id: int) -> None:
    TelegramAccount.objects.filter(id=account_id, user__telegram_user_id=telegram_user_id).delete()


async def terminate_account_session(*, telegram_user_id: int, account_id: int) -> bool:
    import os
    from cryptography.fernet import Fernet
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    account = await get_account_for_user(telegram_user_id=telegram_user_id, account_id=account_id)
    if account is None:
        return False
    if account.status == "connected" and account.encrypted_session:
        key = os.getenv("TELEGRAM_SESSION_ENCRYPTION_KEY")
        if not key:
            raise ValueError("TELEGRAM_SESSION_ENCRYPTION_KEY غير موجود.")
        session = Fernet(key.encode()).decrypt(account.encrypted_session.encode()).decode()
        client = TelegramClient(StringSession(session), int(os.environ["TELEGRAM_API_ID"]), os.environ["TELEGRAM_API_HASH"])
        await client.connect()
        try:
            await client.log_out()
        finally:
            await client.disconnect()
    await delete_account_record(account_id=account_id, telegram_user_id=telegram_user_id)
    return True
