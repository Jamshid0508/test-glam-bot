import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot


class MultiStoreTests(unittest.TestCase):
    def setUp(self):
        self.previous_db_path = bot.DB_PATH
        self.previous_database_url = bot.DATABASE_URL
        self.temp_dir = tempfile.TemporaryDirectory()
        bot.DB_PATH = Path(self.temp_dir.name) / "multi.sqlite3"
        bot.DATABASE_URL = ""

    def tearDown(self):
        bot.DB_PATH = self.previous_db_path
        bot.DATABASE_URL = self.previous_database_url
        self.temp_dir.cleanup()

    def test_stores_seeded_and_existing_stock_goes_to_main(self):
        stores = bot.list_stores()
        self.assertEqual(len(stores), 2)
        self.assertEqual(stores[0]["kind"], "main")
        self.assertEqual(stores[1]["name"], "Qamashi Glam")
        item = bot.inventory_create("Sag", "dona", 3, 100_000, 150_000)
        self.assertEqual(item["store_id"], bot.main_store_id())

    def test_users_auth_and_lockout(self):
        user = bot.create_user("sotuvchi1", "parol123", "Ali", "seller")
        self.assertNotIn("password_hash", user)
        ok, err = bot.authenticate_user("sotuvchi1", "parol123")
        self.assertIsNone(err)
        self.assertEqual(ok["login"], "sotuvchi1")
        bad, err = bot.authenticate_user("sotuvchi1", "xato")
        self.assertIsNone(bad)
        self.assertIsNotNone(err)
        with self.assertRaises(ValueError):
            bot.create_user("sotuvchi1", "boshqa123", "Dup", "seller")
        for _ in range(5):
            bot.authenticate_user("sotuvchi1", "xato")
        locked, err = bot.authenticate_user("sotuvchi1", "parol123")
        self.assertIsNone(locked)
        self.assertIn("urinish", err)

    def test_branch_items_numbers_and_sale_by_number(self):
        branch = bot.branch_store()
        # Same name as a main-store product must not collide
        bot.inventory_create("Sag", "dona", 5, 100_000, 150_000)
        branch_item = bot.inventory_create(
            "Sag", "dona", 2, 100_000, 150_000,
            store_id=branch["id"], item_numbers=["QG-001", "QG-002"],
        )
        self.assertEqual(branch_item["store_id"], branch["id"])
        with self.assertRaises(ValueError):
            bot.add_inventory_items(branch_item["id"], branch["id"], ["QG-001"])

        seller = bot.create_user("filial1", "parol123", "Vali", "seller", branch["id"])
        item, status, sale = bot.sell_branch_item(
            branch["id"], "QG-001", user_id=seller["id"]
        )
        self.assertEqual(status, "ok")
        self.assertEqual(sale["sale_price"], 150_000)
        after = bot.get_item_by_number(branch["id"], "QG-001")
        self.assertEqual(after["status"], "sold")
        _, status2, _ = bot.sell_branch_item(branch["id"], "QG-001")
        self.assertEqual(status2, "already_sold")
        _, status3, _ = bot.sell_branch_item(branch["id"], "QG-999")
        self.assertEqual(status3, "not_found")

    def test_settlement_debt_and_overpay(self):
        branch = bot.branch_store()
        seller = bot.create_user("filial2", "parol123", "Vali", "seller", branch["id"])
        product = bot.inventory_create(
            "Aziya", "dona", 3, 100_000, 200_000,
            store_id=branch["id"], item_numbers=["A-1", "A-2", "A-3"],
        )
        bot.sell_branch_item(branch["id"], "A-1", user_id=seller["id"])
        bot.sell_branch_item(branch["id"], "A-2", user_id=seller["id"])
        today = bot.datetime.now(bot.SHOP_TIMEZONE).date().isoformat()

        sales = bot.store_day_sales(branch["id"], today, seller["id"])
        self.assertEqual(sales["revenue"], 400_000)

        # Seller hands over 300k of 400k -> 100k dated debt
        st = bot.create_settlement(branch["id"], seller["id"], today, 300_000)
        self.assertEqual(st["debt_amount"], 100_000)
        debts = bot.seller_debts(seller_id=seller["id"])
        self.assertEqual(len(debts), 1)
        self.assertEqual(debts[0]["date"], today)
        self.assertEqual(debts[0]["remaining"], 100_000)

        # Partial repayment
        bot.record_debt_payment(st["id"], 40_000)
        debts = bot.seller_debts(seller_id=seller["id"])
        self.assertEqual(debts[0]["remaining"], 60_000)
        self.assertEqual(bot.seller_outstanding_debt(seller["id"]), 60_000)

        # Same day, one more sale (200k) and seller brings 260k more:
        # settlements merge per day — total sales 600k, received 560k,
        # debt_amount 40k, minus the 40k manual payment -> nothing owed.
        bot.sell_branch_item(branch["id"], "A-3", user_id=seller["id"])
        st2 = bot.create_settlement(branch["id"], seller["id"], today, 260_000)
        self.assertEqual(st2["id"], st["id"])
        self.assertEqual(st2["sales_total"], 600_000)
        self.assertEqual(st2["received_amount"], 560_000)
        self.assertEqual(st2["debt_amount"], 40_000)
        self.assertEqual(bot.seller_outstanding_debt(seller["id"]), 0)


    def test_branch_restock_adds_numbers_and_stock(self):
        branch = bot.branch_store()
        product = bot.inventory_create(
            "Sag", "dona", 1, 100_000, 150_000,
            store_id=branch["id"], item_numbers=["R-1"],
        )
        updated = bot.branch_restock(product["id"], ["R-2", "R-3"])
        self.assertEqual(updated["quantity"], 3)
        items = bot.list_items(branch["id"], status="in_stock")
        self.assertEqual(len(items), 3)
        with self.assertRaises(ValueError):
            bot.branch_restock(product["id"], ["R-2"])

    def test_parse_simple_price(self):
        self.assertEqual(bot.parse_simple_price("450000"), (450000.0, None))
        self.assertEqual(bot.parse_simple_price("$75"), (None, 75.0))
        self.assertEqual(bot.parse_simple_price("0"), (None, None))
        with self.assertRaises(ValueError):
            bot.parse_simple_price("narx")


    def test_sales_and_stats_separated_by_store(self):
        branch = bot.branch_store()
        self.assertEqual(branch["address"], "Lelin ko'chasi")
        overview = bot.branch_overview_text(branch["id"])
        self.assertIn(branch["address"], overview)
        bot.inventory_create("AsosiyGilam", "dona", 5, 100_000, 300_000)
        bot.inventory_create(
            "FilialGilam", "dona", 5, 100_000, 200_000,
            store_id=branch["id"], item_numbers=["F-1", "F-2", "F-3", "F-4", "F-5"],
        )
        bot.inventory_sell("AsosiyGilam", 1)
        bot.sell_branch_item(branch["id"], "F-1")
        today = bot.datetime.now(bot.SHOP_TIMEZONE).date().isoformat()

        main_summary = bot.daily_sales_summary(today, store_id=bot.main_store_id())
        branch_summary = bot.daily_sales_summary(today, store_id=branch["id"])
        self.assertEqual(main_summary["revenue"], 300_000)
        self.assertEqual(branch_summary["revenue"], 200_000)
        all_summary = bot.daily_sales_summary(today)
        self.assertEqual(all_summary["revenue"], 500_000)

        stats = bot.overall_inventory_stats()
        by_name = {s["name"]: s for s in stats["stores"]}
        self.assertEqual(by_name["Asosiy do'kon"]["models"], 1)
        self.assertEqual(by_name["Qamashi Glam"]["models"], 1)
        self.assertEqual(by_name["Qamashi Glam"]["stock_value_uzs"], 400_000)


class BotAuthFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous_db_path = bot.DB_PATH
        self.previous_database_url = bot.DATABASE_URL
        self.previous_admin_id = bot.TELEGRAM_ADMIN_ID
        self.temp_dir = tempfile.TemporaryDirectory()
        bot.DB_PATH = Path(self.temp_dir.name) / "auth.sqlite3"
        bot.DATABASE_URL = ""
        bot.TELEGRAM_ADMIN_ID = "111"

    def tearDown(self):
        bot.DB_PATH = self.previous_db_path
        bot.DATABASE_URL = self.previous_database_url
        bot.TELEGRAM_ADMIN_ID = self.previous_admin_id
        self.temp_dir.cleanup()

    def _update(self, telegram_id, text):
        return SimpleNamespace(
            effective_user=SimpleNamespace(id=telegram_id),
            message=SimpleNamespace(text=text, reply_text=AsyncMock()),
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
        )

    def test_current_user_bootstrap_admin_and_stranger(self):
        admin_update = self._update(111, "hi")
        user = bot.current_user(admin_update)
        self.assertEqual(user["role"], "admin")
        stranger = self._update(999, "hi")
        self.assertIsNone(bot.current_user(stranger))

    async def test_login_flow_links_telegram(self):
        branch = bot.branch_store()
        bot.create_user(
            "sotuvchi9",
            "parol123",
            "Test Sotuvchi",
            "seller",
            branch["id"],
        )
        context = SimpleNamespace(user_data={"auth_step": "login"})
        upd = self._update(222, "sotuvchi9")
        await bot.auth_or_seller_text(upd, context)
        self.assertEqual(context.user_data.get("auth_step"), "password")
        upd2 = self._update(222, "parol123")
        await bot.auth_or_seller_text(upd2, context)
        linked = bot.get_user_by_telegram(222)
        self.assertIsNotNone(linked)
        self.assertEqual(linked["login"], "sotuvchi9")
        seller_keyboard = upd2.message.reply_text.await_args.kwargs["reply_markup"].keyboard
        seller_buttons = [button.text for row in seller_keyboard for button in row]
        self.assertIn("📦 Ombor", seller_buttons)
        self.assertIn("🔢 Raqam bilan sotish", seller_buttons)
        self.assertNotIn("👥 Foydalanuvchilar", seller_buttons)
        self.assertIn(branch["name"], upd2.message.reply_text.await_args.args[0])

        bot.inventory_create(
            "SellerOmborGilam",
            "dona",
            1,
            100_000,
            150_000,
            store_id=branch["id"],
            item_numbers=["SELLER-001"],
        )
        warehouse_update = self._update(222, "📦 Ombor")
        await bot.auth_or_seller_text(warehouse_update, context)
        warehouse_message = warehouse_update.effective_message.reply_text
        self.assertIn(
            "SellerOmborGilam",
            warehouse_message.await_args.args[0],
        )

        # Wrong password does not link another telegram account
        context2 = SimpleNamespace(user_data={"auth_step": "login"})
        await bot.auth_or_seller_text(self._update(333, "sotuvchi9"), context2)
        await bot.auth_or_seller_text(self._update(333, "xato"), context2)
        self.assertIsNone(bot.get_user_by_telegram(333))

    async def test_seller_menu_cancels_stale_item_number_mode(self):
        branch = bot.branch_store()
        seller = bot.create_user(
            "menu-seller",
            "parol123",
            "Menyu Sotuvchi",
            "seller",
            branch["id"],
        )
        bot.link_user_telegram(seller["id"], 445)
        bot.inventory_create(
            "Menu Carpet",
            "dona",
            1,
            100_000,
            150_000,
            store_id=branch["id"],
            item_numbers=["001"],
        )
        context = SimpleNamespace(user_data={"awaiting_item_number": True})
        update = self._update(445, "📦 Ombor")

        await bot.auth_or_seller_text(update, context)

        response = update.effective_message.reply_text.await_args.args[0]
        self.assertIn(branch["name"], response)
        self.assertIn("Menu Carpet", response)
        self.assertNotIn("awaiting_item_number", context.user_data)

    async def test_seller_restock_selects_product_before_plain_numbers(self):
        branch = bot.branch_store()
        seller = bot.create_user(
            "restock-seller",
            "parol123",
            "Raqam Sotuvchi",
            "seller",
            branch["id"],
        )
        bot.link_user_telegram(seller["id"], 446)
        product = bot.inventory_create(
            "Restock Carpet",
            "dona",
            1,
            100_000,
            150_000,
            store_id=branch["id"],
            item_numbers=["009"],
        )
        context = SimpleNamespace(user_data={})
        menu_update = self._update(446, "➕ Raqam qo‘shish")
        await bot.auth_or_seller_text(menu_update, context)
        buttons = menu_update.message.reply_text.await_args.kwargs["reply_markup"].inline_keyboard
        self.assertEqual(
            buttons[0][0].callback_data,
            f"sellerrestock:{product['id']}",
        )

        query = SimpleNamespace(
            data=f"sellerrestock:{product['id']}",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        callback_update = SimpleNamespace(
            effective_user=SimpleNamespace(id=446),
            callback_query=query,
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
            message=SimpleNamespace(text="", reply_text=AsyncMock()),
        )
        await bot.seller_restock_callback(callback_update, context)
        await bot.auth_or_seller_text(self._update(446, "010, 011"), context)

        numbers = bot.list_items(branch["id"], status="in_stock")
        self.assertEqual({item["number"] for item in numbers}, {"009", "010", "011"})

    async def test_seller_add_prompts_for_missing_price_and_saves_to_own_store(self):
        branch = bot.branch_store()
        seller = bot.create_user(
            "voice-seller",
            "parol123",
            "Ovoz Sotuvchi",
            "seller",
            branch["id"],
        )
        bot.link_user_telegram(seller["id"], 447)
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_message=message)
        context = SimpleNamespace(user_data={})

        await bot.stage_inventory_add(
            update,
            context,
            {
                "name": "VoiceCarpet",
                "quantity": 2,
                "unit": "dona",
                "store_id": branch["id"],
            },
            "VoiceCarpet 2 dona",
        )

        self.assertTrue(context.user_data["entering_price_for_pending"])
        self.assertIn("sotish narxini kiriting", message.reply_text.await_args.args[0])
        await bot.auth_or_seller_text(self._update(447, "250000"), context)
        self.assertEqual(
            context.user_data["pending_inventory"]["sale_price"],
            250_000,
        )

        query = SimpleNamespace(
            data="inv:save",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        callback_update = SimpleNamespace(
            effective_user=SimpleNamespace(id=447),
            callback_query=query,
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
            message=SimpleNamespace(text="", reply_text=AsyncMock()),
        )
        await bot.inventory_callback(callback_update, context)

        branch_products = bot.inventory_list(store_id=branch["id"])
        self.assertEqual(len(branch_products), 1)
        self.assertEqual(branch_products[0]["name"], "VoiceCarpet")
        self.assertEqual(branch_products[0]["quantity"], 2)
        main_products = bot.inventory_list(store_id=bot.main_store_id())
        self.assertEqual(main_products, [])

    async def test_admin_branch_add_flow(self):
        branch = bot.branch_store()
        context = SimpleNamespace(user_data={})
        query = SimpleNamespace(
            data="bradd:start",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        upd = SimpleNamespace(
            effective_user=SimpleNamespace(id=111),
            callback_query=query,
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
            message=SimpleNamespace(text="", reply_text=AsyncMock()),
        )
        await bot.branch_admin_callback(upd, context)
        self.assertEqual(context.user_data["br_add"]["store_id"], branch["id"])

        async def say(text):
            await bot.handle_text(self._update(111, text), context)

        await say("Filial Gilami 2x3")
        await say("900000")
        await say("1400000")
        await say("QG-201, QG-202")
        rows = bot.inventory_list(store_id=branch["id"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], 2)
        items = bot.list_items(branch["id"], status="in_stock")
        self.assertEqual([i["number"] for i in items], ["QG-201", "QG-202"])

    async def test_admin_settlement_flow_creates_dated_debt(self):
        branch = bot.branch_store()
        seller = bot.create_user("filial10", "parol123", "Sotuvchi A", "seller", branch["id"])
        product = bot.inventory_create(
            "Aziya", "dona", 2, 100_000, 200_000,
            store_id=branch["id"], item_numbers=["S-1", "S-2"],
        )
        bot.sell_branch_item(branch["id"], "S-1", user_id=seller["id"])
        context = SimpleNamespace(user_data={})

        query = SimpleNamespace(
            data=f"settle:seller:{seller['id']}",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        upd = SimpleNamespace(
            effective_user=SimpleNamespace(id=111),
            callback_query=query,
            effective_message=SimpleNamespace(reply_text=AsyncMock()),
            message=SimpleNamespace(text="", reply_text=AsyncMock()),
        )
        await bot.settle_callback(upd, context)
        self.assertEqual(context.user_data.get("settle_amount_for"), seller["id"])

        upd2 = self._update(111, "150000")
        await bot.handle_text(upd2, context)
        debts = bot.seller_debts(seller_id=seller["id"])
        self.assertEqual(len(debts), 1)
        self.assertEqual(debts[0]["remaining"], 50_000)
        today = bot.datetime.now(bot.SHOP_TIMEZONE).date().isoformat()
        self.assertEqual(debts[0]["date"], today)
        text, _ = bot.debts_text_and_markup()
        self.assertIn("50 000", text)
        self.assertIn(today, text)

    async def test_seller_kirim_flow_with_numbers(self):
        branch = bot.branch_store()
        seller = bot.create_user("filial9", "parol123", "Filialchi", "seller", branch["id"])
        bot.link_user_telegram(seller["id"], 444)
        context = SimpleNamespace(user_data={})

        async def say(text):
            await bot.auth_or_seller_text(self._update(444, text), context)

        await say("➕ Gilam qo‘shish")
        await say("Sag 3x4")
        await say("$75")
        await say("1500000")
        await say("QG-101, QG-102")
        rows = bot.inventory_list(store_id=branch["id"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], 2)
        items = bot.list_items(branch["id"], status="in_stock")
        self.assertEqual([i["number"] for i in items], ["QG-101", "QG-102"])
        # And the seller can sell one by number right away
        await say("🔢 Raqam bilan sotish")
        await say("QG-101")
        after = bot.get_item_by_number(branch["id"], "QG-101")
        self.assertEqual(after["status"], "sold")


if __name__ == "__main__":
    unittest.main()
