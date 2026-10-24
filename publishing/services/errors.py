from telethon.errors import (
    AuthKeyUnregisteredError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    ChatSendPlainForbiddenError,
    ChatWriteForbiddenError,
    PeerFloodError,
    PeerIdInvalidError,
    UserBannedInChannelError,
)

GROUP_ERRORS = (ChatSendPlainForbiddenError, ChatWriteForbiddenError,
                UserBannedInChannelError, ChatAdminRequiredError,
                ChannelPrivateError, PeerIdInvalidError)
SESSION_ERRORS = (AuthKeyUnregisteredError,)


def is_group_error(error: BaseException) -> bool:
    return isinstance(error, GROUP_ERRORS)


def is_session_error(error: BaseException) -> bool:
    return isinstance(error, SESSION_ERRORS)


def is_peer_flood(error: BaseException) -> bool:
    return isinstance(error, PeerFloodError)
