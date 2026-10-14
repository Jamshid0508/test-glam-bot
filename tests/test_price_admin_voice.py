import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from starlette.requests import Request

import app
import bot


def _local_request(body=None, method="POST"):
    payload = json.dumps(body).encode() if body is not None else b""

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": method,
            "path": "/api/test",
            "headers": [
                (b"authorization", b"local-dev"),
                (b"content-type", b"application/json"),
            ],
            "client": ("127.0.0.1", 54321),
            "server": ("127.0.0.1", 8000),
            "scheme": "http",
        },
        receive=receive,
    )


def _local_admin():
    return (
        patch.object(bot, "LOCAL_WEBAPP_DEV", True),
        patch.object(app, "TELEGRAM_ADMIN_ID", "910471611"),
        patch.dict(os.environ, {"VERCEL": ""}),
    )


class PriceRequiredTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous_db_path = bot.DB_PATH
        self.previous_database_url = bot.DATABASE_URL
        self.temp_dir = tempfile.TemporaryDirectory()
        bot.DB_PATH = Path(self.temp_dir.name) / "price.sqlite3"
        bot.DATABASE_URL = ""

    def tearDown(self):
        bot.DB_PATH = self.previous_db_path
        bot.DATABASE_URL = self.previous_database_url
        self.temp_dir.cleanup()

    async def test_inv_save_without_price_prompts_instead_of_saving(self):
        query = SimpleNamespace(
            data="inv:save", answer=AsyncMock(), edit_message_text=AsyncMock()
        )
        context = SimpleNamespace(
            user_data={
                "pending_inventory": {
                    "name": "Narxsiz Gilam",
                    "quantity": 2,
                    "unit": "dona",
                    "purchase_price": None,
                    "sale_price": None,
                }
            }
        )
        await bot.inventory_callback(SimpleNamespace(callback_query=query), context)

        self.assertTrue(context.user_data["entering_price_for_pending"])
        self.assertIn("shart", query.edit_message_text.await_args.args[0].lower())
        self.assertIn("pending_inventory", context.user_data)
        self.assertIsNone(
            bot.inventory_get_by_key(bot.inventory_key_for("Narxsiz Gilam"))
        )

    async def test_inv_save_with_price_saves(self):
        query = SimpleNamespace(
            data="inv:save", answer=AsyncMock(), edit_message_text=AsyncMock()
        )
        context = SimpleNamespace(
            user_data={
                "pending_inventory": {
                    "name": "Narxli Gilam",
                    "quantity": 2,
                    "unit": "dona",
                    "purchase_price": 100_000,
                    "sale_price": 150_000,
                }
            }
        )
        await bot.inventory_callback(SimpleNamespace(callback_query=query), context)

        self.assertNotIn("pending_inventory", context.user_data)
        added = bot.inventory_get_by_key(bot.inventory_key_for("Narxli Gilam"))
        self.assertIsNotNone(added)
        self.assertEqual(added["quantity"], 2)

    async def test_skip_price_button_now_requires_price(self):
        query = SimpleNamespace(
            data="inv:skip_price",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                "pending_inventory": {
                    "name": "Elen",
                    "quantity": 1,
                    "unit": "dona",
                    "sale_price": None,
                }
            }
        )
        await bot.inventory_callback(SimpleNamespace(callback_query=query), context)

        self.assertTrue(context.user_data["entering_price_for_pending"])
        self.assertIn(
            "shart", query.edit_message_text.await_args.args[0].lower()
        )

    async def test_admin_price_entry_requires_sale_price(self):
        pending = {
            "name": "Elen",
            "quantity": 1,
            "unit": "dona",
            "purchase_price": None,
            "sale_price": None,
        }
        context = SimpleNamespace(
            user_data={
                "pending_inventory": pending,
                "entering_price_for_pending": True,
            }
        )
        purchase_only = SimpleNamespace(
            text="kelish 100000", reply_text=AsyncMock()
        )
        await bot.handle_text(SimpleNamespace(message=purchase_only), context)

        self.assertTrue(context.user_data["entering_price_for_pending"])
        self.assertEqual(pending["purchase_price"], 100_000)
        self.assertIsNone(pending["sale_price"])

        sale = SimpleNamespace(text="250000", reply_text=AsyncMock())
        await bot.handle_text(SimpleNamespace(message=sale), context)

        self.assertNotIn("entering_price_for_pending", context.user_data)
        self.assertEqual(pending["sale_price"], 250_000)

    async def test_br_add_sale_step_rejects_skip(self):
        branch = bot.branch_store()
        context = SimpleNamespace(
            user_data={
                "br_add": {
                    "step": "sale",
                    "name": "Filial Gilami",
                    "store_id": branch["id"],
                    "admin": True,
                }
            }
        )
        skip = SimpleNamespace(text="0", reply_text=AsyncMock())
        await bot.handle_text(SimpleNamespace(message=skip), context)

        self.assertEqual(context.user_data["br_add"]["step"], "sale")
        self.assertIn("shart", skip.reply_text.await_args.args[0].lower())

        priced = SimpleNamespace(text="1400000", reply_text=AsyncMock())
        await bot.handle_text(SimpleNamespace(message=priced), context)

        self.assertEqual(context.user_data["br_add"]["step"], "numbers")
        self.assertEqual(context.user_data["br_add"]["sale_som"], 1_400_000)

    async def test_bulk_rejects_new_items_without_price(self):
        context = SimpleNamespace(user_data={"adding_bulk_inventory": True})
        message = SimpleNamespace(
            text="Yangi model | 2 | dona", reply_text=AsyncMock()
        )
        await bot.handle_text(SimpleNamespace(message=message), context)

        self.assertIn("shart", message.reply_text.await_args.args[0].lower())
        self.assertTrue(context.user_data["adding_bulk_inventory"])
        self.assertIsNone(
            bot.inventory_get_by_key(bot.inventory_key_for("Yangi model"))
        )

        priced = SimpleNamespace(
            text="Yangi model | 2 | dona | 50000 | 80000",
            reply_text=AsyncMock(),
        )
        await bot.handle_text(SimpleNamespace(message=priced), context)

        added = bot.inventory_get_by_key(bot.inventory_key_for("Yangi model"))
        self.assertIsNotNone(added)
        self.assertEqual(
            bot.current_product_prices(added["id"])["sale_price"], 80_000
        )

    async def test_api_create_product_allows_missing_sale_price(self):
        first, second, third = _local_admin()
        with first, second, third:
            created = await app.api_create_product(
                _local_request(
                    {
                        "name": "NoPrice",
                        "unit": "dona",
                        "quantity": 1,
                    }
                )
            )
        self.assertEqual(created["name"], "NoPrice")
        self.assertIsNone(bot.current_product_prices(created["id"])["sale_price"])

    async def test_api_create_product_calculates_total_usd_purchase_cost(self):
        first, second, third = _local_admin()
        with first, second, third:
            created = await app.api_create_product(
                _local_request(
                    {
                        "name": "SquareCostCarpet",
                        "unit": "dona",
                        "dimensions": "3x4",
                        "quantity": 1,
                        "price_per_sqm_usd": 3,
                    }
                )
            )

        prices = bot.current_product_prices(created["id"])
        self.assertEqual(created["area_sqm"], 12)
        self.assertEqual(prices["price_per_sqm_usd"], 3)
        self.assertEqual(prices["purchase_price_usd"], 36)

    async def test_main_store_zero_stock_product_can_be_replenished(self):
        product = bot.inventory_create(
            "Main Restock 3x4",
            "dona",
            1,
            100_000,
            None,
            dimensions="3x4",
            area_sqm=12,
            item_numbers=["MAIN-001"],
        )
        _, status, _ = bot.sell_branch_item(
            bot.main_store_id(), "MAIN-001", sale_price=200_000
        )
        self.assertEqual(status, "ok")
        self.assertEqual(bot.inventory_get(product["id"])["quantity"], 0)

        first, second, third = _local_admin()
        with first, second, third:
            updated = await app.api_edit_product(
                product["id"],
                _local_request(
                    {
                        "name": "Main Restock 3x4",
                        "unit": "dona",
                        "quantity": 0,
                        "purchase_price_usd": 36,
                        "price_per_sqm_usd": 3,
                        "item_numbers": ["MAIN-002", "MAIN-003"],
                    },
                    method="PATCH",
                ),
            )

        self.assertEqual(updated["quantity"], 2)
        self.assertIsNone(updated["archived_at"])
        self.assertEqual(
            bot.current_product_prices(product["id"])["purchase_price_usd"],
            36,
        )


class AdminBlockProtectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous_db_path = bot.DB_PATH
        self.previous_database_url = bot.DATABASE_URL
        self.temp_dir = tempfile.TemporaryDirectory()
        bot.DB_PATH = Path(self.temp_dir.name) / "adminblock.sqlite3"
        bot.DATABASE_URL = ""

    def tearDown(self):
        bot.DB_PATH = self.previous_db_path
        bot.DATABASE_URL = self.previous_database_url
        self.temp_dir.cleanup()

    def test_set_user_active_rejects_admin(self):
        admin = bot.create_user("boss", "parol123", "Boss", "admin")
        seller = bot.create_user("sotuvchi", "parol123", "Ali", "seller")

        with self.assertRaises(ValueError):
            bot.set_user_active(admin["id"], False)
        self.assertTrue(bot.get_user(admin["id"])["is_active"])

        bot.set_user_active(seller["id"], False)
        self.assertFalse(bot.get_user(seller["id"])["is_active"])
        bot.set_user_active(seller["id"], True)
        self.assertTrue(bot.get_user(seller["id"])["is_active"])

    def test_delete_user_rejects_admin(self):
        admin = bot.create_user("boss", "parol123", "Boss", "admin")
        seller = bot.create_user("sotuvchi", "parol123", "Ali", "seller")

        with self.assertRaises(ValueError):
            bot.delete_user(admin["id"])
        self.assertIsNotNone(bot.get_user(admin["id"]))

        self.assertTrue(bot.delete_user(seller["id"]))
        self.assertIsNone(bot.get_user(seller["id"]))

    async def test_users_toggle_callback_protects_admin(self):
        admin = bot.create_user("boss", "parol123", "Boss", "admin")
        query = SimpleNamespace(
            data=f"usr:toggle:{admin['id']}",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        await bot.users_callback(
            SimpleNamespace(callback_query=query), SimpleNamespace(user_data={})
        )

        self.assertIn(
            "bloklab bo'lmaydi", query.edit_message_text.await_args.args[0]
        )
        self.assertTrue(bot.get_user(admin["id"])["is_active"])

    async def test_user_list_action_cancels_add_wizard(self):
        seller = bot.create_user("sotuvchi", "parol123", "Ali", "seller")
        query = SimpleNamespace(
            data=f"usr:toggle:{seller['id']}",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                "usr_add": {
                    "step": "password",
                    "login": "newuser",
                    "password": "temporary",
                }
            }
        )

        await bot.users_callback(
            SimpleNamespace(callback_query=query), context
        )

        self.assertNotIn("usr_add", context.user_data)
        self.assertFalse(bot.get_user(seller["id"])["is_active"])

    async def test_main_menu_action_cancels_add_wizard(self):
        message = SimpleNamespace(
            text="📊 Bugungi sotuv",
            reply_text=AsyncMock(),
        )
        context = SimpleNamespace(
            user_data={
                "usr_add": {
                    "step": "password",
                    "login": "newuser",
                    "password": "temporary",
                }
            }
        )
        update = SimpleNamespace(message=message, effective_message=message)

        with patch.object(bot, "daily_sales", new=AsyncMock()) as daily_sales:
            await bot.handle_text(update, context)

        daily_sales.assert_awaited_once_with(update, context)
        self.assertNotIn("usr_add", context.user_data)

    async def test_other_admin_inline_action_cancels_add_wizard(self):
        handler = AsyncMock()
        guarded = bot.admin_callback(handler)
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=100),
            callback_query=SimpleNamespace(data="wh:refresh"),
        )
        context = SimpleNamespace(user_data={"usr_add": {"password": "temporary"}})

        with patch.object(bot, "is_admin_user", return_value=True):
            await guarded(update, context)

        handler.assert_awaited_once_with(update, context)
        self.assertNotIn("usr_add", context.user_data)

    def test_users_markup_has_no_admin_toggle(self):
        admin = bot.create_user("boss", "parol123", "Boss", "admin")
        seller = bot.create_user("sotuvchi", "parol123", "Ali", "seller")

        markup = bot.users_list_markup()
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertNotIn(f"usr:toggle:{admin['id']}", callbacks)
        self.assertIn(f"usr:toggle:{seller['id']}", callbacks)
        self.assertNotIn(f"usr:delete:{admin['id']}", callbacks)
        self.assertIn(f"usr:delete:{seller['id']}", callbacks)

    async def test_users_delete_callback_requires_confirmation(self):
        seller = bot.create_user("sotuvchi", "parol123", "Ali", "seller")
        query = SimpleNamespace(
            data=f"usr:delete:{seller['id']}",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        await bot.users_callback(
            SimpleNamespace(callback_query=query), SimpleNamespace(user_data={})
        )

        self.assertIsNotNone(bot.get_user(seller["id"]))
        self.assertIn("tasdiqlaysizmi", query.edit_message_text.await_args.args[0])
        markup = query.edit_message_text.await_args.kwargs["reply_markup"]
        callbacks = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
        ]
        self.assertIn(f"usr:delete_confirm:{seller['id']}", callbacks)
        self.assertIn("usr:delete_cancel", callbacks)

    async def test_users_delete_confirmation_deletes_seller(self):
        seller = bot.create_user("sotuvchi", "parol123", "Ali", "seller")
        query = SimpleNamespace(
            data=f"usr:delete_confirm:{seller['id']}",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        await bot.users_callback(
            SimpleNamespace(callback_query=query), SimpleNamespace(user_data={})
        )

        self.assertIsNone(bot.get_user(seller["id"]))
        self.assertIn("Foydalanuvchi o'chirildi", query.edit_message_text.await_args.args[0])

    async def test_api_toggle_user_rejects_admin(self):
        admin = bot.create_user("boss", "parol123", "Boss", "admin")
        seller = bot.create_user("sotuvchi", "parol123", "Ali", "seller")

        first, second, third = _local_admin()
        with first, second, third:
            with self.assertRaises(HTTPException) as raised:
                await app.api_toggle_user(admin["id"], _local_request(method="GET"))
        self.assertEqual(raised.exception.status_code, 403)
        self.assertTrue(bot.get_user(admin["id"])["is_active"])

        with first, second, third:
            toggled = await app.api_toggle_user(
                seller["id"], _local_request(method="GET")
            )
        self.assertFalse(toggled["is_active"])


class VoiceCommandTests(unittest.TestCase):
    def setUp(self):
        self.previous_db_path = bot.DB_PATH
        self.previous_database_url = bot.DATABASE_URL
        self.temp_dir = tempfile.TemporaryDirectory()
        bot.DB_PATH = Path(self.temp_dir.name) / "voice.sqlite3"
        bot.DATABASE_URL = ""

    def tearDown(self):
        bot.DB_PATH = self.previous_db_path
        bot.DATABASE_URL = self.previous_database_url
        self.temp_dir.cleanup()

    def test_fallback_models_have_no_bogus_entries(self):
        self.assertNotIn("gemini-3.8-flash", bot.GEMINI_FALLBACK_MODELS)
        self.assertIn("gemini-2.0-flash", bot.GEMINI_FALLBACK_MODELS)

    def test_voice_to_command_single_call(self):
        bot.set_usd_rate(12_850)
        audio_path = Path(self.temp_dir.name) / "sample.ogg"
        audio_path.write_bytes(b"test audio")
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(
                    return_value=SimpleNamespace(
                        text=json.dumps(
                            {
                                "transcript": (
                                    "Prado 3x4 2 dona keldi "
                                    "kelish narxi kvadratiga 3 dollar"
                                ),
                                "name": "Prado",
                                "dimensions": "3x4",
                                "width": 3,
                                "length": 4,
                                "area_sqm": 12,
                                "quantity": 2,
                                "unit": "dona",
                                "price_per_sqm_usd": 3,
                                "purchase_price_usd": None,
                                "purchase_price": None,
                                "sale_price_usd": None,
                                "sale_price": None,
                                "action": "add",
                            }
                        )
                    )
                )
            )
        )
        with patch.object(bot, "gemini_clients", [client]), patch.object(
            bot, "inventory_list_full", return_value=[{"name": "Prado"}]
        ):
            transcript, parsed = bot.gemini_voice_to_command(audio_path)

        self.assertEqual(
            transcript,
            "Prado 3x4 2 dona keldi kelish narxi kvadratiga 3 dollar",
        )
        self.assertEqual(parsed["name"], "Prado 3x4")
        self.assertEqual(parsed["quantity"], 2.0)
        self.assertEqual(parsed["action"], "add")
        self.assertEqual(parsed["purchase_price_usd"], 36.0)
        self.assertEqual(parsed["purchase_price"], 462_600.0)
        client.models.generate_content.assert_called_once()

    def test_voice_to_command_missing_transcript_fails_fast(self):
        audio_path = Path(self.temp_dir.name) / "empty.ogg"
        audio_path.write_bytes(b"test audio")
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(
                    return_value=SimpleNamespace(
                        text=json.dumps({"transcript": "", "name": None})
                    )
                )
            )
        )
        with patch.object(bot, "gemini_clients", [client]), patch.object(
            bot, "inventory_list_full", return_value=[]
        ):
            with self.assertRaises(ValueError):
                bot.gemini_voice_to_command(audio_path)

        client.models.generate_content.assert_called_once()

    def test_normalize_ai_command_returns_none_without_name(self):
        self.assertIsNone(bot._normalize_ai_command({"name": None}, "Prado 3x4"))
        self.assertIsNone(bot._normalize_ai_command({"name": "  "}, "Prado 3x4"))
        self.assertIsNone(bot._normalize_ai_command(["not", "dict"], "Prado 3x4"))

    def test_apply_corrections_patches_parsed_name(self):
        parsed = {"name": "Sank 3x4", "quantity": 2}
        result = bot._apply_voice_corrections_to_parsed(
            parsed, [("Sank", "Sag")]
        )
        self.assertEqual(result["name"], "Sag 3x4")


if __name__ == "__main__":
    unittest.main()
