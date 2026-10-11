import hashlib
import hmac
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from fastapi import HTTPException
from starlette.requests import Request

import app
import bot
from app import verify_telegram_init_data


class TelegramWebAppAuthTests(unittest.TestCase):
    bot_token = "123456:local-test-token"
    admin_id = "910471611"

    def create_init_data(self, user_id=None, auth_date=None):
        values = {
            "auth_date": str(auth_date or int(time.time())),
            "query_id": "AAE-test-query",
            "user": json.dumps(
                {
                    "id": int(user_id or self.admin_id),
                    "first_name": "Test",
                },
                separators=(",", ":"),
            ),
        }
        check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(values.items())
        )
        secret_key = hmac.new(
            b"WebAppData",
            self.bot_token.encode(),
            hashlib.sha256,
        ).digest()
        values["hash"] = hmac.new(
            secret_key,
            check_string.encode(),
            hashlib.sha256,
        ).hexdigest()
        return urlencode(values)

    def test_accepts_signed_admin_init_data(self):
        user = verify_telegram_init_data(
            self.create_init_data(),
            self.bot_token,
            self.admin_id,
        )

        self.assertEqual(user["id"], int(self.admin_id))

    def test_rejects_signed_data_from_non_admin(self):
        with self.assertRaises(HTTPException) as raised:
            verify_telegram_init_data(
                self.create_init_data(user_id=123),
                self.bot_token,
                self.admin_id,
            )

        self.assertEqual(raised.exception.status_code, 403)

    def test_rejects_expired_init_data(self):
        with self.assertRaises(HTTPException) as raised:
            verify_telegram_init_data(
                self.create_init_data(auth_date=int(time.time()) - 86401),
                self.bot_token,
                self.admin_id,
            )

        self.assertEqual(raised.exception.status_code, 401)

    def test_rejects_tampered_init_data(self):
        init_data = self.create_init_data().replace("first_name", "last_name")

        with self.assertRaises(HTTPException) as raised:
            verify_telegram_init_data(
                init_data,
                self.bot_token,
                self.admin_id,
            )

        self.assertEqual(raised.exception.status_code, 401)

    def test_local_test_auth_only_works_on_loopback_and_outside_vercel(self):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/bootstrap",
            "headers": [(b"authorization", b"local-dev")],
            "client": ("127.0.0.1", 54321),
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
        }
        request = Request(scope)
        with patch.object(app.bot, "LOCAL_WEBAPP_DEV", True), patch.dict(
            os.environ,
            {"TELEGRAM_ADMIN_ID": self.admin_id},
            clear=True,
        ):
            local_user = app.require_admin(request)
            self.assertEqual(local_user["id"], int(self.admin_id))

            os.environ["VERCEL"] = "1"
            with self.assertRaises(HTTPException) as raised:
                app.require_admin(request)

        self.assertEqual(raised.exception.status_code, 401)


class TelegramBotAdminTests(unittest.IsolatedAsyncioTestCase):
    def test_application_requires_a_numeric_admin_id(self):
        with patch.object(bot, "BOT_TOKEN", "123456:local-test-token"), patch.object(
            bot,
            "TELEGRAM_ADMIN_ID",
            "",
        ):
            with self.assertRaisesRegex(RuntimeError, "TELEGRAM_ADMIN_ID"):
                bot.build_application()

    def test_admin_user_is_matched_against_configured_telegram_id(self):
        with patch.object(bot, "TELEGRAM_ADMIN_ID", "910471611"):
            self.assertTrue(bot.is_admin_user(SimpleNamespace(id=910471611)))
            self.assertFalse(bot.is_admin_user(SimpleNamespace(id=123)))
            self.assertFalse(bot.is_admin_user(None))

    async def test_unauthorized_callback_is_stopped_and_alerted(self):
        callback = AsyncMock()
        query = SimpleNamespace(answer=AsyncMock())
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=123),
            callback_query=query,
        )
        guarded = bot.admin_callback(callback)

        with patch.object(bot, "TELEGRAM_ADMIN_ID", "910471611"):
            await guarded(update, SimpleNamespace())

        callback.assert_not_awaited()
        query.answer.assert_awaited_once_with(
            "Botdan foydalanishga ruxsat yo‘q.",
            show_alert=True,
        )

    async def test_telegram_error_handler_hides_exception_details_from_user(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = bot.Update(update_id=1, message=message)
        context = SimpleNamespace(error=RuntimeError("private backend details"))

        await bot.telegram_error_handler(update, context)

        response = message.reply_text.await_args.args[0]
        self.assertIn("texnik xatolik", response)
        self.assertNotIn("private backend details", response)


if __name__ == "__main__":
    unittest.main()
