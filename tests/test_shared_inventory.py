import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

import app
import bot


class SharedInventoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous_db_path = bot.DB_PATH
        self.previous_database_url = bot.DATABASE_URL
        self.temp_dir = tempfile.TemporaryDirectory()
        bot.DB_PATH = Path(self.temp_dir.name) / "shared.sqlite3"
        bot.DATABASE_URL = ""

    def tearDown(self):
        bot.DB_PATH = self.previous_db_path
        bot.DATABASE_URL = self.previous_database_url
        self.temp_dir.cleanup()

    async def test_web_panel_reads_products_created_by_bot(self):
        product = bot.inventory_create(
            "Bir xil ombor gilami",
            "dona",
            4,
            100_000,
            150_000,
        )
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/products",
                "headers": [(b"authorization", b"local-dev")],
                "client": ("127.0.0.1", 54321),
                "server": ("127.0.0.1", 8000),
                "scheme": "http",
            }
        )

        with patch.object(bot, "LOCAL_WEBAPP_DEV", True), patch.object(
            app, "TELEGRAM_ADMIN_ID", "910471611"
        ), patch.dict(os.environ, {"VERCEL": ""}):
            products = await app.api_products(request)

        self.assertEqual([item["id"] for item in products], [product["id"]])
        self.assertEqual(products[0]["quantity"], 4)
        self.assertNotIn("article", products[0])
        self.assertNotIn("name_key", products[0])

    async def test_web_panel_price_edits_are_visible_to_the_bot(self):
        product = bot.inventory_create(
            "Webdan tahrirlangan gilam",
            "dona",
            4,
            100_000,
            150_000,
        )
        payload = json.dumps(
            {
                "name": product["name"],
                "unit": product["unit"],
                "quantity": product["quantity"],
                "purchase_price": 120_000,
                "sale_price": 180_000,
            }
        ).encode()

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "PATCH",
                "path": f"/api/products/{product['id']}",
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

        with patch.object(bot, "LOCAL_WEBAPP_DEV", True), patch.object(
            app, "TELEGRAM_ADMIN_ID", "910471611"
        ), patch.dict(os.environ, {"VERCEL": ""}):
            await app.api_edit_product(product["id"], request)

        prices = bot.current_product_prices(product["id"])
        self.assertEqual(prices["purchase_price"], 120_000)
        self.assertEqual(prices["sale_price"], 180_000)

    async def test_duplicate_product_name_returns_uzbek_message(self):
        bot.inventory_create("Sag gilami", "metr", 7, None, None)
        payload = json.dumps(
            {"name": "Sag gilami", "unit": "metr", "quantity": 5}
        ).encode()

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/products",
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

        with patch.object(bot, "LOCAL_WEBAPP_DEV", True), patch.object(
            app, "TELEGRAM_ADMIN_ID", "910471611"
        ), patch.dict(os.environ, {"VERCEL": ""}):
            with self.assertRaises(HTTPException) as raised:
                await app.api_create_product(request)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("bazada bor", raised.exception.detail)

    async def test_bulk_bot_entry_skips_existing_names_and_adds_new_ones(self):
        bot.inventory_create("Sag gilami", "metr", 7, None, None)
        message = SimpleNamespace(
            text=(
                "Sag | 5 | metr\n"
                "Sag gilami | 3 | metr\n"
                "Yangi model | 2 | dona"
            ),
            reply_text=AsyncMock(),
        )
        context = SimpleNamespace(user_data={"adding_bulk_inventory": True})

        await bot.handle_text(SimpleNamespace(message=message), context)

        response = message.reply_text.await_args.args[0]
        self.assertIn("Bazaga qo‘shildi: 1 ta", response)
        self.assertIn("Bazadagi gilamlar", response)
        self.assertIn("Ro‘yxat ichidagi takror nomlar: 1 ta", response)
        existing = bot.inventory_get_by_key(bot.inventory_key_for("Sag"))
        added = bot.inventory_get_by_key(bot.inventory_key_for("Yangi model"))
        self.assertEqual(existing["quantity"], 7)
        self.assertEqual(added["quantity"], 2)

    async def test_settings_and_product_usd_api(self):
        # 1. Update USD rate via API
        payload = json.dumps({"usd_rate": 12900}).encode("utf-8")
        async def receive_post():
            return {"type": "http.request", "body": payload, "more_body": False}
        request_post = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/settings",
                "headers": [(b"authorization", b"local-dev")],
                "client": ("127.0.0.1", 54321),
                "server": ("127.0.0.1", 8000),
                "scheme": "http",
            },
            receive=receive_post,
        )
        with patch.object(bot, "LOCAL_WEBAPP_DEV", True), patch.object(
            app, "TELEGRAM_ADMIN_ID", "910471611"
        ), patch.dict(os.environ, {"VERCEL": ""}):
            res_rate = await app.api_set_settings(request_post)
        self.assertEqual(res_rate["usd_rate"], 12900.0)

        # 2. Create product with dimensions and USD purchase price
        prod_payload = json.dumps({
            "name": "Sag 3x4",
            "unit": "dona",
            "quantity": 2,
            "dimensions": "3x4",
            "area_sqm": 12.0,
            "price_per_sqm_usd": 3.0,
            "purchase_price_usd": 36.0,
            "purchase_price": 464400.0,
            "sale_price": 600000.0,
        }).encode("utf-8")
        async def receive_prod():
            return {"type": "http.request", "body": prod_payload, "more_body": False}
        request_prod = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/products",
                "headers": [(b"authorization", b"local-dev")],
                "client": ("127.0.0.1", 54321),
                "server": ("127.0.0.1", 8000),
                "scheme": "http",
            },
            receive=receive_prod,
        )
        with patch.object(bot, "LOCAL_WEBAPP_DEV", True), patch.object(
            app, "TELEGRAM_ADMIN_ID", "910471611"
        ), patch.dict(os.environ, {"VERCEL": ""}):
            created = await app.api_create_product(request_prod)
        self.assertEqual(created["dimensions"], "3x4")
        self.assertEqual(created["purchase_price_usd"], 36.0)
        self.assertEqual(created["price_per_sqm_usd"], 3.0)

        # 3. Sell 1 piece and check dashboard USD profit and revenue
        bot.inventory_sell("Sag 3x4", 1.0, sale_price=600000.0)
        request_dash = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/dashboard",
                "headers": [(b"authorization", b"local-dev")],
                "client": ("127.0.0.1", 54321),
                "server": ("127.0.0.1", 8000),
                "scheme": "http",
            }
        )
        with patch.object(bot, "LOCAL_WEBAPP_DEV", True), patch.object(
            app, "TELEGRAM_ADMIN_ID", "910471611"
        ), patch.dict(os.environ, {"VERCEL": ""}):
            dashboard = await app.api_dashboard(request_dash)
        self.assertEqual(dashboard["usd_rate"], 12900.0)
        self.assertEqual(dashboard["revenue"], 600000.0)
        self.assertAlmostEqual(dashboard["revenue_usd"], round(600000.0 / 12900.0, 2))
        self.assertEqual(dashboard["profit"], 600000.0 - 464400.0)
        self.assertAlmostEqual(dashboard["profit_usd"], round((600000.0 - 464400.0) / 12900.0, 2))

    async def test_api_products_export(self):
        bot.inventory_create("Afshon Zangori", "dona", 8, 120_000, 180_000)
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/products/export",
                "headers": [(b"authorization", b"local-dev")],
                "client": ("127.0.0.1", 54321),
                "server": ("127.0.0.1", 8000),
                "scheme": "http",
            }
        )
        with patch.object(bot, "LOCAL_WEBAPP_DEV", True), patch.object(
            app, "TELEGRAM_ADMIN_ID", "910471611"
        ), patch.dict(os.environ, {"VERCEL": ""}):
            response = await app.api_products_export(request)

        self.assertEqual(response.media_type, "text/csv; charset=utf-8")
        self.assertIn("qamashi-gilam-ombor-", response.headers["Content-Disposition"])
        content_text = response.body.decode("utf-8-sig")
        self.assertIn("Afshon Zangori", content_text)
        self.assertIn("8", content_text)


if __name__ == "__main__":
    unittest.main()
