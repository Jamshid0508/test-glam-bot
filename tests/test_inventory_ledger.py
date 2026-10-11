import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import bot


class InventoryLedgerTests(unittest.TestCase):
    def setUp(self):
        self.previous_db_path = bot.DB_PATH
        self.previous_database_url = bot.DATABASE_URL
        self.temp_dir = tempfile.TemporaryDirectory()
        bot.DB_PATH = Path(self.temp_dir.name) / "inventory.sqlite3"
        bot.DATABASE_URL = ""

    def tearDown(self):
        bot.DB_PATH = self.previous_db_path
        bot.DATABASE_URL = self.previous_database_url
        self.temp_dir.cleanup()

    def test_sale_uses_current_prices_and_records_profit(self):
        product = bot.inventory_create(
            "Katta Qizil Gul",
            "dona",
            10,
            100_000,
            150_000,
        )

        quantity, status, sale = bot.inventory_sell("Katta Qizil Gul", 2)

        self.assertEqual((quantity, status), (8, "ok"))
        self.assertEqual(sale["purchase_price"], 100_000)
        self.assertEqual(sale["sale_price"], 150_000)
        self.assertEqual(sale["profit"], 100_000)
        self.assertEqual(product["name"], "Katta Qizil Gul")

    def test_price_changes_are_effective_dated_for_profit(self):
        product = bot.inventory_create(
            "Elen Classic",
            "dona",
            8,
            100_000,
            150_000,
        )
        first_sale = bot.inventory_sell("Elen Classic", 1)[2]
        first_sale_row = bot.movement_history(product["id"])[0]

        _, status = bot.inventory_edit(
            product["id"],
            "Elen Classic",
            "dona",
            7,
            120_000,
            180_000,
        )
        second_sale = bot.inventory_sell("Elen Classic", 1)[2]
        edited_sale, edit_status = bot.inventory_update_sale(
            first_sale["id"],
            1,
            None,
            first_sale_row["created_at"],
        )

        self.assertEqual(status, "ok")
        self.assertEqual(edit_status, "ok")
        self.assertEqual(first_sale["profit"], 50_000)
        self.assertEqual(second_sale["profit"], 60_000)
        self.assertEqual(edited_sale["purchase_price"], 100_000)
        self.assertEqual(edited_sale["sale_price"], 150_000)
        self.assertEqual(edited_sale["profit"], 50_000)
        self.assertEqual(len(bot.product_price_history(product["id"])), 2)

    def test_editing_and_deleting_sale_reconciles_stock(self):
        bot.inventory_create("Sag gilami", "metr", 9, 200_000, 250_000)
        _, status, sale = bot.inventory_sell("Sag gilami", 2)
        self.assertEqual(status, "ok")

        updated, status = bot.inventory_update_sale(
            sale["id"],
            3,
            280_000,
            bot.shop_now(),
        )
        self.assertEqual(status, "ok")
        self.assertEqual(updated["stock"], 6)
        self.assertEqual(updated["profit"], 240_000)

        restored, status = bot.inventory_delete_sale(sale["id"])
        self.assertEqual((restored, status), (9, "ok"))

    def test_sale_cannot_drive_stock_below_zero(self):
        bot.inventory_create("Yangi model", "dona", 1, 10_000, 20_000)

        quantity, status, sale = bot.inventory_sell("Yangi model", 2)

        self.assertEqual((quantity, status, sale), (1, "insufficient", None))

    def test_sale_with_wrong_unit_is_rejected_without_changing_stock(self):
        product = bot.inventory_create("Birlik nazorati", "dona", 5, 10_000, 20_000)

        quantity, status, sale = bot.inventory_sell(
            "Birlik nazorati",
            2,
            unit="metr",
        )

        self.assertEqual((quantity, status, sale), (5, "unit_mismatch", None))
        self.assertEqual(bot.inventory_get(product["id"])["quantity"], 5)
        self.assertEqual(len(bot.movement_history(product["id"])), 1)

    def test_purchase_with_wrong_unit_is_rejected_without_changing_stock(self):
        product = bot.inventory_create("Birlikli gilam", "dona", 5, 10_000, 20_000)
        original_movements = bot.movement_history(product["id"])

        with self.assertRaises(bot.InventoryUnitMismatchError):
            bot.inventory_add("Birlikli gilam", "metr", 3)

        current = bot.inventory_get(product["id"])
        self.assertEqual(current["quantity"], 5)
        self.assertEqual(current["unit"], "dona")
        self.assertEqual(bot.movement_history(product["id"]), original_movements)

    def test_archiving_hides_product_but_preserves_history(self):
        product = bot.inventory_create(
            "Elen Silk",
            "dona",
            2,
            50_000,
            80_000,
        )
        _, _, sale = bot.inventory_sell("Elen Silk", 1)

        self.assertTrue(bot.inventory_archive(product["id"]))
        self.assertNotIn(
            product["id"],
            {row["id"] for row in bot.inventory_list_full()},
        )
        self.assertEqual(len(bot.movement_history(product["id"])), 2)
        self.assertTrue(bot.inventory_restore(product["id"]))

    def test_voice_parser_keeps_multiword_name_and_labeled_prices(self):
        parsed = bot.parse_command(
            "Yangi ko'k rangli gilam 4 dona keldi, "
            "kelish narxi 1 200 000 sotish narxi 1 450 000"
        )

        self.assertEqual(parsed["name"], "Yangi ko'k rangli")
        self.assertEqual(parsed["purchase_price"], 1_200_000)
        self.assertEqual(parsed["sale_price"], 1_450_000)

    def test_pending_prices_accept_two_dollar_values_without_sqm_conversion(self):
        bot.set_usd_rate(12_850)

        parsed = bot.parse_pending_inventory_prices("30$ 40$")

        self.assertEqual(parsed["purchase_price_usd"], 30)
        self.assertEqual(parsed["purchase_price"], 385_500)
        self.assertEqual(parsed["sale_price"], 514_000)
        self.assertNotIn("price_per_sqm_usd", parsed)

    def test_pending_prices_accept_grouped_sum_values_and_single_sale(self):
        bot.set_usd_rate(12_850)

        parsed_pair = bot.parse_pending_inventory_prices("1 500 000 2 000 000")
        parsed_single = bot.parse_pending_inventory_prices("2 000 000")

        self.assertEqual(parsed_pair["purchase_price"], 1_500_000)
        self.assertEqual(parsed_pair["sale_price"], 2_000_000)
        self.assertEqual(parsed_single, {"sale_price": 2_000_000})

    def test_inventory_names_are_unique_and_voice_name_is_corrected(self):
        bot.inventory_create(
            "Katta Qizil Gul gilami",
            "dona",
            3,
            100_000,
            150_000,
        )

        with self.assertRaises(Exception):
            bot.inventory_create(
                "Katta Qizil Gul",
                "metr",
                2,
                100_000,
                150_000,
            )

        self.assertEqual(
            bot.inventory_match_name("Kata Qizil Gul"),
            "Katta Qizil Gul gilami",
        )
        quantity, status, _ = bot.inventory_sell("Katta Qizil Gul gilami", 1)
        self.assertEqual((quantity, status), (2, "ok"))

    def test_legacy_inventory_migrates_to_name_key(self):
        with sqlite3.connect(bot.DB_PATH) as connection:
            connection.execute(
                """
                CREATE TABLE inventory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    article TEXT UNIQUE NOT NULL,
                    name TEXT DEFAULT '',
                    unit TEXT NOT NULL DEFAULT 'dona',
                    quantity REAL NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                INSERT INTO inventory(article, name, unit, quantity, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("old-article-1", "Katta Qizil Gilam", "dona", 2, bot.shop_now()),
            )
        connection.close()

        bot.db().close()

        migrated = bot.inventory_get_by_key(bot.inventory_key_for("Katta Qizil"))
        self.assertIsNotNone(migrated)
        self.assertEqual(migrated["quantity"], 2)
        with self.assertRaises(sqlite3.IntegrityError):
            bot.inventory_create("Katta Qizil", "dona", 1, None, None)

    def test_gemini_prompt_includes_long_inventory_name(self):
        long_name = (
            "Katta To'q Ko'k Rangli Klassik Gul Naqshli Yumshoq "
            "Premium Mehmonxona Gilami"
        )
        bot.inventory_create(long_name, "dona", 1, None, None)
        audio_path = Path(self.temp_dir.name) / "sample.ogg"
        audio_path.write_bytes(b"audio")
        prompts = []

        def transcribe(self, **kwargs):
            prompts.append(kwargs["contents"][0])
            return type("Response", (), {"text": "audio text"})()

        client = type(
            "Client",
            (),
            {"models": type("Models", (), {"generate_content": transcribe})()},
        )()
        with patch.object(bot, "gemini_clients", [client]):
            result = bot.gemini_transcribe(audio_path)

        self.assertEqual(result, "audio text")
        self.assertIn(long_name, prompts[0])
        self.assertNotIn("artikul", prompts[0].lower())

    def test_bulk_inventory_input_validates_all_rows_before_saving(self):
        items, error = bot.parse_bulk_inventory_input(
            "Sag | 7 | metr\nKatta Qizil Gul | 10 | dona"
        )
        self.assertIsNone(error)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["name"], "Sag")
        self.assertEqual(items[1]["quantity"], 10)

        items, error = bot.parse_bulk_inventory_input(
            "Sag | 7 | metr\nNoto‘g‘ri qator"
        )
        self.assertIsNone(items)
        self.assertIn("2-qator", error)


if __name__ == "__main__":
    unittest.main()
