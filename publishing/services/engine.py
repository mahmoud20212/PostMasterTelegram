import asyncio
import logging
import os
import random
import sys
import time
from datetime import datetime, timedelta

from asgiref.sync import sync_to_async
from django.utils import timezone
from django.db import OperationalError
from telethon import TelegramClient
from telethon import types as telethon_types
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, SlowModeWaitError

from messages.models import SavedMessage
from publishing.models import PublishLog, PublishingRun, PublishingSettings
from telegram_accounts.models import TelegramAccount
from telegram_groups.models import TelegramGroup
from users.models import User
from publishing.services.errors import is_group_error, is_peer_flood, is_session_error
from publishing.services.policy import mark_limit_notified, release_send_slot, reserve_send_slot
from publishing.services.runs import (
    ELIGIBLE_GROUP_STATUSES, claim_run, create_run_async, finish_run_async,
    run_is_active, run_policy, send_target_allowed,
)
from publishing.services.contention import is_sqlite_contention

logger = logging.getLogger(__name__)
TYPING_DURATION_SECONDS = 1.5


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


@sync_to_async
def get_publish_snapshot(telegram_user_id: int, run_id=None) -> dict:
    user = User.objects.get(telegram_user_id=telegram_user_id)
    settings, _ = PublishingSettings.objects.get_or_create(user=user)
    account = TelegramAccount.objects.filter(
        user=user,
        status="connected",
        is_publishing_account=True,
    ).exclude(encrypted_session="").first()
    if account is None:
        account = TelegramAccount.objects.filter(user=user, status="connected").exclude(encrypted_session="").first()
    if run_id is not None:
        run = PublishingRun.objects.filter(pk=run_id, user=user, active=True).select_related("account").first()
        account = run.account if run else None
    messages = list(SavedMessage.objects.filter(user=user, is_active=True).order_by("created_at"))
    # الصلاحية تُفحص عملياً عند الإرسال؛ المجموعة المستخرجة تلقائياً تكون
    # محددة للنشر حتى أول محاولة، ثم تُعطّل فقط إذا رفضها Telegram.
    groups = list(TelegramGroup.objects.filter(user=user, is_selected=True, status__in=ELIGIBLE_GROUP_STATUSES).order_by("created_at"))
    return {
        "account": account,
        "messages": messages,
        "groups": groups,
        "cycle_duration_minutes": settings.cycle_duration_minutes,
        "sleep_enabled": settings.sleep_enabled,
        "work_hours": settings.work_hours,
        "sleep_hours": settings.sleep_hours,
        "message_index": settings.current_message_index,
        "group_index": settings.current_group_index,
        "cycle_started_at": settings.cycle_started_at,
        "next_cycle_at": settings.next_cycle_at,
        "policy": None,
    }


@sync_to_async
def advance_cycle_indices(*, telegram_user_id: int, run_id, message_index: int, group_index: int) -> None:
    PublishingSettings.objects.filter(
        user__telegram_user_id=telegram_user_id, user__publishing_runs__id=run_id,
        user__publishing_runs__active=True, user__is_publishing=True,
    ).update(current_message_index=message_index, current_group_index=group_index)


@sync_to_async
def set_cycle_started_at(*, telegram_user_id: int, run_id, started_at) -> None:
    PublishingSettings.objects.filter(
        user__telegram_user_id=telegram_user_id, user__publishing_runs__id=run_id,
        user__publishing_runs__active=True, user__is_publishing=True,
    ).update(cycle_started_at=started_at)


@sync_to_async
def set_next_cycle_at(*, telegram_user_id: int, run_id, next_cycle_at) -> None:
    PublishingSettings.objects.filter(
        user__telegram_user_id=telegram_user_id, user__publishing_runs__id=run_id,
        user__publishing_runs__active=True, user__is_publishing=True,
    ).update(next_cycle_at=next_cycle_at)


@sync_to_async
def mark_account_restricted(account_id: int, status: str, error: str, paused_until=None) -> None:
    TelegramAccount.objects.filter(pk=account_id).update(
        status=status, last_error=error, last_error_at=timezone.now(),
        restriction_type=status, paused_until=paused_until,
    )


@sync_to_async
def disable_group(group_id: int, error: str) -> None:
    TelegramGroup.objects.filter(pk=group_id).update(
        can_publish=False, status="disabled", last_error=error,
    )


@sync_to_async
def write_publish_log(telegram_user_id: int, account_id: int, group_id: int, message_id: int,
                      status: str, error: BaseException | None = None,
                      restriction_seconds: int | None = None) -> None:
    PublishLog.objects.create(
        user_id=User.objects.get(telegram_user_id=telegram_user_id).pk,
        account_id=account_id,
        group_id=group_id,
        message_id=message_id,
        status=status,
        error_type=type(error).__name__ if error else "",
        error_message=str(error) if error else "",
        restriction_seconds=restriction_seconds,
    )


async def _send_cycle(
    telegram_user_id: int,
    client: TelegramClient,
    cooldowns: dict[str, float],
    deadline,
    run_id,
    account,
) -> None:
    snapshot = await get_publish_snapshot(telegram_user_id, run_id)
    policy = await run_policy(telegram_user_id, run_id, account.pk, account.encrypted_session)
    if policy is None:
        await finish_run_async(telegram_user_id, run_id)
        return
    messages = snapshot["messages"]
    groups = snapshot["groups"]
    if not messages or not groups:
        return
    pairs = build_cycle_pairs(
        message_count=len(messages),
        group_count=len(groups),
        message_index=snapshot["message_index"] % len(messages),
        group_index=snapshot["group_index"] % len(groups),
    )
    for message_position, group_position in pairs:
        if timezone.now() >= deadline:
            break
        if not await run_is_active(telegram_user_id, run_id):
            return
        saved_message = messages[message_position]
        group = groups[group_position]
        cooldown_until = cooldowns.get(group.identifier, 0)
        if cooldown_until <= time.monotonic():
            reserved = False
            try:
                async with client.action(group.identifier, "typing"):
                    await asyncio.sleep(TYPING_DURATION_SECONDS)
                    policy = await run_policy(telegram_user_id, run_id, account.pk, account.encrypted_session)
                    if policy is None:
                        await finish_run_async(telegram_user_id, run_id)
                        return
                    if not await send_target_allowed(telegram_user_id, group.pk, saved_message.pk):
                        continue
                    if not await reserve_send_slot(telegram_user_id, policy["daily_send_limit"]):
                        await mark_limit_notified(telegram_user_id)
                        await finish_run_async(telegram_user_id, run_id)
                        return
                    reserved = True
                    # Recheck after quota reservation, immediately before network I/O.
                    if await run_policy(telegram_user_id, run_id, account.pk, account.encrypted_session) is None:
                        await release_send_slot(telegram_user_id)
                        reserved = False
                        await finish_run_async(telegram_user_id, run_id)
                        return
                    if not await send_target_allowed(telegram_user_id, group.pk, saved_message.pk):
                        await release_send_slot(telegram_user_id)
                        reserved = False
                        continue
                    await client.send_message(
                        group.identifier,
                        saved_message.text,
                        formatting_entities=build_telethon_entities(saved_message.entities),
                    )
                # await write_publish_log(telegram_user_id, snapshot["account"].pk, group.pk, saved_message.pk, "success")
            except SlowModeWaitError as error:
                if reserved:
                    await release_send_slot(telegram_user_id)
                # await write_publish_log(telegram_user_id, snapshot["account"].pk, group.pk, saved_message.pk, "failed", error)
                cooldowns[group.identifier] = time.monotonic() + error.seconds
                logger.warning("Skipping group %s for %s seconds due to Telegram rate limit", group.identifier, error.seconds)
            except FloodWaitError as error:
                if reserved:
                    await release_send_slot(telegram_user_id)
                # await write_publish_log(telegram_user_id, snapshot["account"].pk, group.pk, saved_message.pk, "failed", error, error.seconds)
                until = timezone.now() + timedelta(seconds=error.seconds)
                await mark_account_restricted(account.pk, "paused_flood_wait", str(error), until)
                await finish_run_async(telegram_user_id, run_id)
                return
            except Exception as error:
                if reserved:
                    await release_send_slot(telegram_user_id)
                # await write_publish_log(telegram_user_id, snapshot["account"].pk, group.pk, saved_message.pk, "failed", error)
                if is_peer_flood(error):
                    await mark_account_restricted(account.pk, "restricted", str(error))
                    await finish_run_async(telegram_user_id, run_id)
                    return
                if is_session_error(error):
                    await mark_account_restricted(account.pk, "unauthorized", str(error))
                    await finish_run_async(telegram_user_id, run_id)
                    return
                if is_group_error(error):
                    await disable_group(group.pk, str(error))
                    logger.warning("Disabled group %s: %s", group.identifier, error)
                else:
                    raise
        await advance_cycle_indices(
            telegram_user_id=telegram_user_id,
            run_id=run_id,
            message_index=(message_position + 1) % len(messages),
            group_index=(group_position + 1) % len(groups),
        )
        if (message_position, group_position) != pairs[-1]:
            remaining = (deadline - timezone.now()).total_seconds()
            if remaining <= 0:
                break
            interval = calculate_interval(
                min_interval_seconds=policy["min_interval_seconds"],
                max_interval_seconds=policy["max_interval_seconds"],
            )
            await _wait_for_next_cycle(telegram_user_id, min(interval, remaining), run_id)


async def _wait_for_next_cycle(telegram_user_id: int, seconds: float, run_id) -> None:
    remaining = max(0, seconds)
    while remaining > 0 and await run_is_active(telegram_user_id, run_id):
        step = min(1, remaining)
        await asyncio.sleep(step)
        remaining -= step


async def _run(telegram_user_id: int, run_id=None) -> None:
    # Legacy queued tasks have no generation and must never attach to a new run.
    if run_id is None or not await claim_run(telegram_user_id, run_id):
        return
    client = None
    try:
        snapshot = await get_publish_snapshot(telegram_user_id, run_id)
        account = snapshot["account"]
        if account is None or await run_policy(telegram_user_id, run_id, account.pk, account.encrypted_session) is None:
            return
        key = os.getenv("TELEGRAM_SESSION_ENCRYPTION_KEY")
        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        if not key or not api_id or not api_hash:
            raise RuntimeError("Telegram session configuration is incomplete")
        from cryptography.fernet import Fernet

        session = Fernet(key.encode()).decrypt(account.encrypted_session.encode()).decode()
        # Surface rate limits instead of silently sleeping and retrying after Stop.
        client = TelegramClient(StringSession(session), int(api_id), api_hash, flood_sleep_threshold=0)
        await client.connect()
        if not await client.is_user_authorized():
            await mark_account_restricted(account.pk, "unauthorized", "Telegram session is no longer authorized")
            return
        cooldowns: dict[str, float] = {}
        while await run_is_active(telegram_user_id, run_id):
            current = await get_publish_snapshot(telegram_user_id, run_id)
            policy = await run_policy(telegram_user_id, run_id, account.pk, account.encrypted_session)
            if policy is None:
                return
            now = timezone.now()
            if current["next_cycle_at"]:
                if now < current["next_cycle_at"]:
                    await _wait_for_next_cycle(telegram_user_id, int((current["next_cycle_at"] - now).total_seconds()), run_id)
                    continue
                await set_next_cycle_at(telegram_user_id=telegram_user_id, run_id=run_id, next_cycle_at=None)
            if current["sleep_enabled"] and not is_working_time(
                now=timezone.localtime(),
                work_hours=current["work_hours"],
                sleep_hours=current["sleep_hours"],
            ):
                await _wait_for_next_cycle(telegram_user_id, 60, run_id)
                continue
            cycle_started_at = current["cycle_started_at"]
            duration_seconds = current["cycle_duration_minutes"] * 60
            if cycle_started_at is None or (now - cycle_started_at).total_seconds() >= duration_seconds:
                cycle_started_at = now
                await set_cycle_started_at(telegram_user_id=telegram_user_id, run_id=run_id, started_at=cycle_started_at)
            deadline = cycle_started_at + timedelta(seconds=duration_seconds)
            await _send_cycle(telegram_user_id, client, cooldowns, deadline, run_id, account)
            remaining = max(0, int((deadline - timezone.now()).total_seconds()))
            await _wait_for_next_cycle(telegram_user_id, remaining, run_id)
            if await run_is_active(telegram_user_id, run_id) and policy["cycle_rest_seconds"]:
                await set_next_cycle_at(
                    telegram_user_id=telegram_user_id,
                    run_id=run_id,
                    next_cycle_at=timezone.now() + timedelta(seconds=policy["cycle_rest_seconds"]),
                )
    except asyncio.CancelledError:
        raise
    except Exception as error:
        # Exception messages may contain Telegram request data; log the type only.
        logger.error("Publishing failed for user %s run %s (%s)", telegram_user_id, run_id, type(error).__name__)
        raise
    finally:
        primary_error = sys.exc_info()[1]
        cleanup_error = None
        try:
            await finish_run_async(telegram_user_id, run_id)
        except (Exception, asyncio.CancelledError) as error:
            cleanup_error = error
            logger.error("Run cleanup failed for run %s (%s)", run_id, type(error).__name__)
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=10)
            except (Exception, asyncio.CancelledError) as error:
                if cleanup_error is None:
                    cleanup_error = error
                logger.error("Telegram disconnect failed for run %s (%s)", run_id, type(error).__name__)
        if primary_error is None and cleanup_error is not None:
            raise cleanup_error


async def start_publishing(telegram_user_id: int) -> tuple[bool, str]:
    try:
        run, text = await create_run_async(telegram_user_id)
    except OperationalError as error:
        if not await sync_to_async(is_sqlite_contention)(error):
            raise
        return False, "قاعدة البيانات مشغولة مؤقتاً. لم يبدأ النشر؛ حاول مجدداً."
    if run is None:
        return False, text
    from publishing.tasks import publish_user

    try:
        # Broker I/O is synchronous; keep it off the bot event loop.
        await sync_to_async(publish_user.delay, thread_sensitive=False)(telegram_user_id, str(run.pk))
    except Exception:
        try:
            await finish_run_async(telegram_user_id, run.pk)
        except OperationalError:
            logger.error("Unable to invalidate run %s after broker failure; administrative recovery required", run.pk)
            return False, "تعذر تأكيد التشغيل أو إيقاف الجلسة. يلزم فحصها بواسطة المسؤول قبل إعادة المحاولة."
        return False, "تعذر تشغيل محرك النشر. تأكد أن Redis وCelery يعملان."
    return True, text


async def stop_publishing(telegram_user_id: int) -> str:
    try:
        await finish_run_async(telegram_user_id)
    except OperationalError as error:
        if not await sync_to_async(is_sqlite_contention)(error):
            raise
        return "قاعدة البيانات مشغولة مؤقتاً. لم يتم تأكيد الإيقاف؛ حاول مجدداً."
    return "تم إيقاف النشر."
