from asgiref.sync import sync_to_async
import re
import os
from urllib.parse import urlparse

from telegram_groups.models import TelegramGroup
from subscriptions.services.subscriptions import _check_limit
from users.models import User


def normalize_group_identifier(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise ValueError("يرجى إدخال اسم المجموعة أو رابطها أو الـID.")

    candidate = raw
    if raw.startswith("http://") or raw.startswith("https://") or raw.startswith("t.me/"):
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        if parsed.netloc.lower() not in {"t.me", "telegram.me"}:
            raise ValueError("رابط Telegram غير صالح.")
        candidate = parsed.path.strip("/")
    candidate = candidate.removeprefix("@").strip("/")

    if re.fullmatch(r"-?\d{5,20}", candidate):
        return f"id:{candidate}"
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", candidate):
        return f"username:{candidate.lower()}"
    if re.fullmatch(r"\+?[A-Za-z0-9_-]{8,}", candidate):
        return f"invite:{candidate.lower()}"
    raise ValueError("أدخل @username أو رابط t.me أو ID رقمي صحيح.")


@sync_to_async
def create_group(*, telegram_user_id: int, identifier: str) -> TelegramGroup:
    check_limit_sync(telegram_user_id=telegram_user_id, resource="groups")
    user = User.objects.get(telegram_user_id=telegram_user_id)
    canonical = normalize_group_identifier(identifier)
    if TelegramGroup.objects.filter(user=user, canonical_identifier=canonical).exists():
        raise ValueError("هذه المجموعة مضافة مسبقاً لديك.")
    return TelegramGroup.objects.create(
        user=user,
        identifier=identifier.strip(),
        canonical_identifier=canonical,
        title=identifier.strip(),
    )


def check_limit_sync(*, telegram_user_id: int, resource: str) -> None:
    _check_limit(telegram_user_id=telegram_user_id, resource=resource)


@sync_to_async
def list_groups(*, telegram_user_id: int) -> list[TelegramGroup]:
    return list(TelegramGroup.objects.filter(user__telegram_user_id=telegram_user_id))


@sync_to_async
def toggle_group(*, telegram_user_id: int, group_id: int) -> TelegramGroup:
    group = TelegramGroup.objects.get(id=group_id, user__telegram_user_id=telegram_user_id)
    group.is_selected = not group.is_selected
    group.save(update_fields=["is_selected"])
    return group


@sync_to_async
def delete_group(*, telegram_user_id: int, group_id: int) -> None:
    TelegramGroup.objects.filter(id=group_id, user__telegram_user_id=telegram_user_id).delete()


@sync_to_async
def delete_all_groups(*, telegram_user_id: int) -> int:
    deleted, _ = TelegramGroup.objects.filter(user__telegram_user_id=telegram_user_id).delete()
    return deleted


@sync_to_async
def save_extracted_group(*, telegram_user_id: int, identifier: str, title: str) -> bool:
    user = User.objects.get(telegram_user_id=telegram_user_id)
    canonical = normalize_group_identifier(identifier)
    if TelegramGroup.objects.filter(user=user, canonical_identifier=canonical).exists():
        return False
    _check_limit(telegram_user_id=telegram_user_id, resource="groups")
    _, created = TelegramGroup.objects.get_or_create(
        user=user,
        canonical_identifier=canonical,
        defaults={"identifier": identifier, "title": title or identifier},
    )
    return created


async def extract_groups_from_account(*, telegram_user_id: int) -> tuple[int, int]:
    from cryptography.fernet import Fernet
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    account = await _get_connected_account(telegram_user_id=telegram_user_id)
    if account is None:
        raise ValueError("لا يوجد حساب Telegram متصل.")
    key = os.getenv("TELEGRAM_SESSION_ENCRYPTION_KEY")
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not key or not api_id or not api_hash:
        raise ValueError("إعدادات Telegram غير مكتملة.")

    session = Fernet(key.encode()).decrypt(account.encrypted_session.encode()).decode()
    client = TelegramClient(StringSession(session), int(api_id), api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise ValueError("جلسة الحساب غير صالحة.")
        added = 0
        total = 0
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if not dialog.is_group:
                continue
            total += 1
            if getattr(entity, "username", None):
                identifier = f"@{entity.username}"
            else:
                entity_id = str(entity.id)
                identifier = f"-100{entity_id}" if getattr(entity, "megagroup", False) else entity_id
            if await save_extracted_group(
                telegram_user_id=telegram_user_id,
                identifier=identifier,
                title=getattr(entity, "title", None) or dialog.name or identifier,
            ):
                added += 1
        return added, total
    finally:
        await client.disconnect()


@sync_to_async
def _get_connected_account(*, telegram_user_id: int):
    from telegram_accounts.models import TelegramAccount

    return TelegramAccount.objects.filter(
        user__telegram_user_id=telegram_user_id,
        status="connected",
    ).exclude(encrypted_session="").order_by("-is_publishing_account", "id").first()
