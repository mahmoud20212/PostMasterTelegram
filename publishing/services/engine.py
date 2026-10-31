"""Bounded publishing: one item, one network operation, then release the slot."""
import asyncio
import logging
import os
import random
from datetime import datetime

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import OperationalError
from telethon import TelegramClient, types as telethon_types
from telethon.errors import (FloodWaitError, SlowModeWaitError, RPCError,
                             ServerError, RpcCallFailError, TimedOutError)
from telethon.sessions import StringSession

from publishing.models import PublishingRun
from publishing.services.contention import is_sqlite_contention
from publishing.services.errors import is_group_error, is_peer_flood, is_session_error
from publishing.services.execution_lock import execution_lock
from publishing.services.runs import create_run_async, finish_run_async
from publishing.services.steps import begin_send, claim_step, complete_attempt

logger = logging.getLogger(__name__)


def calculate_interval(*, min_interval_seconds: int = 30, max_interval_seconds: int = 30,
                       cycle_duration_minutes: int = 0, message_count: int = 0,
                       group_count: int = 0) -> float:
    minimum = max(30, int(min_interval_seconds or 30))
    maximum = max(minimum, int(max_interval_seconds or minimum))
    return float(random.randint(minimum, maximum))


def is_working_time(*, now: datetime, work_hours: int, sleep_hours: int) -> bool:
    total_hours = max(1, work_hours) + max(1, sleep_hours)
    return (now.hour % total_hours) < max(1, work_hours)


def build_cycle_pairs(*, message_count: int, group_count: int, message_index: int, group_index: int) -> list[tuple[int, int]]:
    count = group_count
    return [
        ((message_index + offset) % message_count, (group_index + offset) % group_count)
        for offset in range(count)
    ] if count else []


def build_telethon_entities(entities: list[dict]) -> list:
    entity_map = {
        "mention": "MessageEntityMention",
        "hashtag": "MessageEntityHashtag",
        "cashtag": "MessageEntityCashtag",
        "bot_command": "MessageEntityBotCommand",
        "url": "MessageEntityUrl",
        "email": "MessageEntityEmail",
        "phone_number": "MessageEntityPhone",
        "bold": "MessageEntityBold",
        "italic": "MessageEntityItalic",
        "underline": "MessageEntityUnderline",
        "strikethrough": "MessageEntityStrike",
        "code": "MessageEntityCode",
        "pre": "MessageEntityPre",
        "text_link": "MessageEntityTextUrl",
        "text_mention": "MessageEntityMentionName",
        "blockquote": "MessageEntityBlockquote",
        "spoiler": "MessageEntitySpoiler",
        "custom_emoji": "MessageEntityCustomEmoji",
    }
    converted = []
    for entity in entities or []:
        class_name = entity_map.get(entity.get("type"))
        entity_class = getattr(telethon_types, class_name, None) if class_name else None
        if entity_class is None:
            continue
        values = {
            "offset": int(entity["offset"]),
            "length": int(entity["length"]),
        }
        if entity["type"] in {"text_link", "pre"}:
            values["url" if entity["type"] == "text_link" else "language"] = entity.get("url", "") if entity["type"] == "text_link" else entity.get("language", "")
        if entity["type"] == "custom_emoji":
            values["document_id"] = int(entity["custom_emoji_id"])
        if entity["type"] == "text_mention":
            continue
        converted.append(entity_class(**values))
    return converted


async def _deliver(payload, clients):
    key = os.getenv("TELEGRAM_SESSION_ENCRYPTION_KEY")
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not key or not api_id or not api_hash:
        raise RuntimeError("Telegram session configuration is incomplete")
    from cryptography.fernet import Fernet

    session = Fernet(key.encode()).decrypt(payload["encrypted_session"].encode()).decode()
    client = TelegramClient(
        StringSession(session), int(api_id), api_hash, flood_sleep_threshold=0,
        request_retries=0, connection_retries=0, auto_reconnect=False,
    )
    clients.append(client)
    await client.connect()
    if not await client.is_user_authorized():
        return "failed", {"error_type": "UnauthorizedSession"}
    # Resolve before the durable sending marker. No Telegram typing action.
    target = payload["target"]
    if target.lstrip("-").isdigit():
        target = int(target)
    peer = await client.get_input_entity(target)
    decision = await sync_to_async(begin_send)(payload["attempt_id"])
    if decision == "defer":
        return "deferred", {"error_type": "WorkRestPaused"}
    if not decision:
        return "skipped", {"error_type": "PermissionChanged"}
    message = await client.send_message(
        peer, payload["text"], formatting_entities=build_telethon_entities(payload["entities"]),
    )
    return "success", {"telegram_message_id": getattr(message, "id", None)}


async def _bounded_delivery(payload):
    clients = []
    try:
        return await asyncio.wait_for(_deliver(payload, clients), timeout=settings.PUBLISHING_STEP_TIMEOUT)
    except (SlowModeWaitError, FloodWaitError) as error:
        return "failed", {"error_type": type(error).__name__, "restriction_seconds": error.seconds}
    except (TimeoutError, OSError, ServerError, RpcCallFailError, TimedOutError) as error:
        # Before 'sending' this is a failure; afterwards delivery is unknown.
        return "interrupted", {"error_type": type(error).__name__}
    except Exception as error:
        if is_group_error(error):
            return "failed", {"error_type": type(error).__name__, "group_rejected": True}
        if is_peer_flood(error) or is_session_error(error) or isinstance(error, RPCError):
            return "failed", {"error_type": type(error).__name__}
        return "interrupted", {"error_type": type(error).__name__}
    finally:
        if clients:
            try:
                await asyncio.wait_for(clients[0].disconnect(), timeout=settings.PUBLISHING_DISCONNECT_TIMEOUT)
            except (Exception, asyncio.CancelledError) as error:
                logger.warning("Telegram cleanup failed (%s)", type(error).__name__)


def execute_step(run_id, dispatch_token):
    owner = PublishingRun.objects.filter(pk=run_id, scheduler_version=2).values_list("user_id", "account_id", "account__telegram_user_id").first()
    if owner is None:
        return
    user_id, account_id, telegram_account_id = owner
    # The OS/DB session releases this guard on process death. Lease expiry by
    # itself never permits a concurrent sender, even across Stop -> Start.
    with execution_lock("user", user_id) as user_acquired:
        if not user_acquired:
            return
        with execution_lock("account", telegram_account_id or account_id) as account_acquired:
            if not account_acquired:
                return
            payload = claim_step(run_id, dispatch_token)
            if payload is None:
                return
            if payload["skip"]:
                outcome, detail = "skipped", {"error_type": payload["skip"]}
            else:
                outcome, detail = asyncio.run(_bounded_delivery(payload))
            # Never retry network I/O when persistence fails. The expired attempt
            # is recovered as unknown and consumed by the scheduler instead.
            complete_attempt(payload["attempt_id"], outcome, **detail)


async def start_publishing(telegram_user_id: int) -> tuple[bool, str]:
    try:
        run, text = await create_run_async(telegram_user_id)
    except OperationalError as error:
        if not await sync_to_async(is_sqlite_contention)(error):
            raise
        return False, "قاعدة البيانات مشغولة مؤقتاً. لم يبدأ النشر؛ حاول مجدداً."
    # The scheduler discovers the committed run even if Redis is unavailable.
    return run is not None, text


async def stop_publishing(telegram_user_id: int) -> str:
    try:
        await finish_run_async(telegram_user_id)
    except OperationalError as error:
        if not await sync_to_async(is_sqlite_contention)(error):
            raise
        return "قاعدة البيانات مشغولة مؤقتاً. لم يتم تأكيد الإيقاف؛ حاول مجدداً."
    return "تم إيقاف النشر."
