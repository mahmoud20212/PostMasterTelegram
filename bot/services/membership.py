import logging

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from django.conf import settings

logger = logging.getLogger(__name__)


async def check_required_membership(bot: Bot, telegram_user_id: int) -> bool | None:
    """None means verification failed, rather than confirmed non-membership."""
    chat_id = settings.ONEPOST_REQUIRED_GROUP_CHAT_ID
    if isinstance(chat_id, str) and chat_id.lstrip("-").isdigit():
        chat_id = int(chat_id)
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=telegram_user_id, request_timeout=5)
    except (TelegramAPIError, TimeoutError, OSError) as error:
        logger.warning("Required group membership check failed (%s)", type(error).__name__)
        return None
    if member.status == ChatMemberStatus.RESTRICTED:
        return member.is_member is True
    return member.status in {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.MEMBER}
