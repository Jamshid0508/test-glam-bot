import unittest
from unittest.mock import AsyncMock

from telegram import MenuButtonWebApp

import bot


class WebAppMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_sets_telegram_menu_button_to_web_panel(self):
        previous_url = bot.WEB_APP_URL
        bot.WEB_APP_URL = "https://gilam-bot.vercel.app/panel"
        application = type(
            "ApplicationStub",
            (),
            {"bot": type("BotStub", (), {"set_chat_menu_button": AsyncMock()})()},
        )()
        try:
            await bot.configure_web_app_menu(application)
        finally:
            bot.WEB_APP_URL = previous_url

        application.bot.set_chat_menu_button.assert_awaited_once()
        menu_button = application.bot.set_chat_menu_button.await_args.kwargs[
            "menu_button"
        ]
        self.assertIsInstance(menu_button, MenuButtonWebApp)
        self.assertEqual(menu_button.text, "Web panel")
        self.assertEqual(
            menu_button.web_app.url,
            "https://gilam-bot.vercel.app/panel",
        )

    async def test_skips_menu_setup_without_https_url(self):
        previous_url = bot.WEB_APP_URL
        bot.WEB_APP_URL = ""
        application = type(
            "ApplicationStub",
            (),
            {"bot": type("BotStub", (), {"set_chat_menu_button": AsyncMock()})()},
        )()
        try:
            await bot.configure_web_app_menu(application)
        finally:
            bot.WEB_APP_URL = previous_url

        application.bot.set_chat_menu_button.assert_not_awaited()

    def test_web_panel_button_is_available_only_with_https_url(self):
        previous_url = bot.WEB_APP_URL
        try:
            bot.WEB_APP_URL = "https://gilam-bot.vercel.app/panel"
            button = bot.web_panel_inline_button()
            self.assertEqual(button.text, "🌐 Web panel")
            self.assertEqual(
                button.web_app.url,
                "https://gilam-bot.vercel.app/panel",
            )

            bot.WEB_APP_URL = "http://127.0.0.1:8000/panel"
            self.assertIsNone(bot.web_panel_inline_button())
        finally:
            bot.WEB_APP_URL = previous_url

    def test_reply_keyboard_has_no_web_panel_placeholder_button(self):
        keyboard = bot.main_menu_markup().keyboard
        button_texts = [button.text for row in keyboard for button in row]

        self.assertNotIn("🌐 Web panel", button_texts)
        self.assertNotIn("🌐 Web panel (deploydan so'ng)", button_texts)
        self.assertEqual(
            button_texts,
            [
                "📦 Ombor",
                "➕ Gilam qo‘shish",
                "📥 Ko‘p gilam kiritish",
                "📊 Bugungi sotuv",
                "📈 Statistika",
                "🏪 Filial",
                "👥 Foydalanuvchilar",
                "💰 Hisob-kitob",
                "📕 Qarzlar",
            ],
        )


if __name__ == "__main__":
    unittest.main()
