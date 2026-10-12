import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import app


class VercelWebhookTests(unittest.IsolatedAsyncioTestCase):
    async def test_production_startup_configures_telegram_webhook(self):
        telegram_app = SimpleNamespace(
            initialize=AsyncMock(),
            bot=SimpleNamespace(set_webhook=AsyncMock()),
            start=AsyncMock(),
            stop=AsyncMock(),
            shutdown=AsyncMock(),
        )
        with (
            patch.object(app, "build_application", return_value=telegram_app),
            patch.object(app, "WEBHOOK_SECRET", "test-webhook-secret"),
            patch.object(app.bot, "configure_web_app_menu", new_callable=AsyncMock),
            patch.dict(
                os.environ,
                {
                    "VERCEL": "1",
                    "VERCEL_ENV": "production",
                    "VERCEL_PROJECT_PRODUCTION_URL": "gilam-bot.vercel.app",
                },
            ),
        ):
            async with app.lifespan(app.app):
                telegram_app.bot.set_webhook.assert_awaited_once_with(
                    url="https://gilam-bot.vercel.app/webhook",
                    secret_token="test-webhook-secret",
                )

        telegram_app.start.assert_awaited_once()
        telegram_app.stop.assert_awaited_once()
        telegram_app.shutdown.assert_awaited_once()

    async def test_preview_startup_does_not_replace_production_webhook(self):
        telegram_app = SimpleNamespace(
            initialize=AsyncMock(),
            bot=SimpleNamespace(set_webhook=AsyncMock()),
            start=AsyncMock(),
            stop=AsyncMock(),
            shutdown=AsyncMock(),
        )
        with (
            patch.object(app, "build_application", return_value=telegram_app),
            patch.object(app, "WEBHOOK_SECRET", "test-webhook-secret"),
            patch.object(app.bot, "configure_web_app_menu", new_callable=AsyncMock),
            patch.dict(
                os.environ,
                {
                    "VERCEL": "1",
                    "VERCEL_ENV": "preview",
                    "VERCEL_PROJECT_PRODUCTION_URL": "gilam-bot.vercel.app",
                },
            ),
        ):
            async with app.lifespan(app.app):
                telegram_app.bot.set_webhook.assert_not_awaited()

        telegram_app.start.assert_awaited_once()
        telegram_app.stop.assert_awaited_once()
        telegram_app.shutdown.assert_awaited_once()
