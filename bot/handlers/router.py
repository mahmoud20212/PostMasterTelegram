from aiogram import Router

from bot.handlers.start import router as start_router
from bot.handlers.messages import router as messages_router
from bot.handlers.groups import router as groups_router
from bot.handlers.accounts import router as accounts_router
from bot.handlers.publishing import router as publishing_router
from bot.handlers.sleep import router as sleep_router
from bot.handlers.reports import router as reports_router
from bot.handlers.subscription import router as subscription_router
from bot.handlers.guide import router as guide_router
from bot.middlewares.membership import RequiredGroupMiddleware


def build_router() -> Router:
    router = Router(name="bot")
    # Inner middleware runs only for matched handlers, including child routers;
    # unrelated group chatter does not cause membership API calls.
    membership = RequiredGroupMiddleware()
    router.message.middleware(membership)
    router.callback_query.middleware(membership)
    router.include_router(start_router)
    router.include_router(messages_router)
    router.include_router(groups_router)
    router.include_router(accounts_router)
    router.include_router(publishing_router)
    router.include_router(sleep_router)
    router.include_router(reports_router)
    router.include_router(subscription_router)
    router.include_router(guide_router)
    return router
