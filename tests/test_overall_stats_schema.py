import tempfile
import unittest
from pathlib import Path

import bot


class OverallInventoryStatsSchemaTests(unittest.TestCase):
    """Regression: overall stats must work with the schema db() creates.

    The inventory table has no purchase_price column (UZS prices live in
    product_prices); overall_inventory_stats used to SELECT it and crash
    with "no such column: purchase_price" on every "Statistika" press.
    """

    def setUp(self):
        self.previous_db_path = bot.DB_PATH
        self.previous_database_url = bot.DATABASE_URL
        self.temp_dir = tempfile.TemporaryDirectory()
        bot.DB_PATH = Path(self.temp_dir.name) / "stats.sqlite3"
        bot.DATABASE_URL = ""

    def tearDown(self):
        bot.DB_PATH = self.previous_db_path
        bot.DATABASE_URL = self.previous_database_url
        self.temp_dir.cleanup()

    def test_overall_stats_work_without_inventory_purchase_price_column(self):
        bot.inventory_create("Statistika gilami", "metr", 5, 200_000, 260_000)

        ov = bot.overall_inventory_stats()

        self.assertEqual(ov["total_models"], 1)
        self.assertEqual(ov["products"][0]["name"], "Statistika gilami")
        # Latest product_prices purchase price drives the stock value.
        self.assertEqual(ov["total_stock_value_uzs"], 5 * 200_000)

    def test_overall_stats_work_with_no_products(self):
        ov = bot.overall_inventory_stats()

        self.assertEqual(ov["total_models"], 0)
        self.assertEqual(ov["total_stock_value_uzs"], 0)


if __name__ == "__main__":
    unittest.main()
