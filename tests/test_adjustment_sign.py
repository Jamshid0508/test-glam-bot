import tempfile
import unittest
from pathlib import Path

import bot


class AdjustmentSignTests(unittest.TestCase):
    """Regression: quantity edits used to record the adjustment movement as
    abs(delta), losing its direction. Movement-based totals then disagreed
    with the real stock (e.g. purchase 4, sale 2, adjustment +1 recorded as
    just "1"). Adjustments must be signed and product_movement_stats must
    reconcile: total_in - total_sold == current stock.
    """

    def setUp(self):
        self.previous_db_path = bot.DB_PATH
        self.previous_database_url = bot.DATABASE_URL
        self.temp_dir = tempfile.TemporaryDirectory()
        bot.DB_PATH = Path(self.temp_dir.name) / "adj.sqlite3"
        bot.DATABASE_URL = ""

    def tearDown(self):
        bot.DB_PATH = self.previous_db_path
        bot.DATABASE_URL = self.previous_database_url
        self.temp_dir.cleanup()

    def test_signed_adjustments_reconcile_with_stock(self):
        item = bot.inventory_create("Sag", "dona", 4, 100_000, 150_000)
        inv_id = item["id"]

        # Sell 2 -> stock 2
        bot.inventory_sell("Sag", 2)
        # Correct stock up by 1 (e.g. panel edit) -> stock 3
        bot.inventory_update(inv_id, "Sag", "dona", 3)
        # Correct stock down by 2 -> stock 1
        bot.inventory_update(inv_id, "Sag", "dona", 1)

        stats = bot.product_movement_stats(inv_id)
        self.assertEqual(stats["total_in"] - stats["total_sold"], 1.0)

        adjustments = [
            r for r in stats["records"] if r["type"] == "adjustment"
        ]
        self.assertEqual(
            sorted(r["quantity"] for r in adjustments), [-2.0, 1.0]
        )


if __name__ == "__main__":
    unittest.main()
