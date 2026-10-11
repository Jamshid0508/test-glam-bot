import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot


class WarehouseQtyPriceRefreshTests(unittest.IsolatedAsyncioTestCase):
    """Regression: after +/- quantity in the bot's product card, the refreshed
    card used plain inventory_get (no price fields), so Kelish/Sotish prices
    disappeared ("—") even though they exist in product_prices and the web
    panel showed them. The refresh must use inventory_get_with_prices.
    """

    def setUp(self):
        self.previous_db_path = bot.DB_PATH
        self.previous_database_url = bot.DATABASE_URL
        self.temp_dir = tempfile.TemporaryDirectory()
        bot.DB_PATH = Path(self.temp_dir.name) / "qty.sqlite3"
        bot.DATABASE_URL = ""

    def tearDown(self):
        bot.DB_PATH = self.previous_db_path
        bot.DATABASE_URL = self.previous_database_url
        self.temp_dir.cleanup()

    async def test_qty_change_keeps_prices_in_detail_card(self):
        item = bot.inventory_create("Sag", "dona", 3, 963_750, 1_200_000)
        query = SimpleNamespace(
            data=f"wh:qty:{item['id']}:1",
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(user_data={})

        await bot.warehouse_callback(update, context)

        text = query.edit_message_text.await_args.args[0]
        self.assertIn("963 750", text)
        self.assertIn("1 200 000", text)
        self.assertEqual(bot.inventory_get_with_prices(item["id"])["quantity"], 4.0)


if __name__ == "__main__":
    unittest.main()
