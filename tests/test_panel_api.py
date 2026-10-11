import json
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException
from starlette.requests import Request

import app
import bot


def make_request(body=None, cookie=None, method="POST"):
    headers = [(b"content-type", b"application/json")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    scope = {
        "type": "http",
        "method": method,
        "path": "/api/test",
        "headers": headers,
        "query_string": b"",
        "client": ("127.0.0.1", 12345),
    }
    raw = json.dumps(body).encode() if body is not None else b""

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(scope, receive)


class PanelApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous_db_path = bot.DB_PATH
        self.previous_database_url = bot.DATABASE_URL
        self.temp_dir = tempfile.TemporaryDirectory()
        bot.DB_PATH = Path(self.temp_dir.name) / "panel.sqlite3"
        bot.DATABASE_URL = ""

    def tearDown(self):
        bot.DB_PATH = self.previous_db_path
        bot.DATABASE_URL = self.previous_database_url
        self.temp_dir.cleanup()

    def test_session_token_roundtrip_and_tamper(self):
        token = app.make_session_token(42)
        payload = app.parse_session_token(token)
        self.assertEqual(payload["uid"], 42)
        self.assertIsNone(app.parse_session_token(token + "x"))
        self.assertIsNone(app.parse_session_token("garbage"))

    async def test_login_cookie_and_role_guards(self):
        bot.create_user("admin1", "parol123", "Boss", "admin")
        branch = bot.branch_store()
        bot.create_user("sotuvchi", "parol123", "Ali", "seller", branch["id"])

        response = await app.api_login(
            make_request({"login": "admin1", "password": "parol123"})
        )
        cookie_header = response.headers["set-cookie"]
        self.assertIn("glam_session=", cookie_header)
        cookie = cookie_header.split(";")[0]
        admin_req = make_request(cookie=cookie, method="GET")
        self.assertEqual(app.panel_user(admin_req)["login"], "admin1")
        app.require_admin(admin_req)  # must not raise

        with self.assertRaises(HTTPException):
            await app.api_login(make_request({"login": "admin1", "password": "xato"}))

        seller_login = await app.api_login(
            make_request({"login": "sotuvchi", "password": "parol123"})
        )
        seller_cookie = seller_login.headers["set-cookie"].split(";")[0]
        seller_req = make_request(cookie=seller_cookie, method="GET")
        with self.assertRaises(HTTPException) as raised:
            app.require_admin(seller_req)
        self.assertEqual(raised.exception.status_code, 403)
        panel_user = app.require_panel_user(seller_req)
        self.assertEqual(panel_user["role"], "seller")

        # Seller only sees own store's products
        bot.inventory_create("AsosiyGilam", "dona", 1, 100, 200)
        bot.inventory_create(
            "FilialGilam", "dona", 1, 100, 200, store_id=branch["id"]
        )
        products = await app.api_products(seller_req)
        names = [p["name"] for p in products]
        self.assertIn("FilialGilam", names)
        self.assertNotIn("AsosiyGilam", names)

    async def test_panel_dashboard_and_sales_store_filter(self):
        bot.create_user("admin3", "parol123", "Boss", "admin")
        branch = bot.branch_store()
        bot.inventory_create("AsosiyGilam", "dona", 5, 100_000, 300_000)
        bot.inventory_create(
            "FilialGilam", "dona", 5, 100_000, 200_000,
            store_id=branch["id"], item_numbers=["D-1", "D-2", "D-3", "D-4", "D-5"],
        )
        bot.inventory_sell("AsosiyGilam", 1)
        bot.sell_branch_item(branch["id"], "D-1")
        login = await app.api_login(
            make_request({"login": "admin3", "password": "parol123"})
        )
        cookie = login.headers["set-cookie"].split(";")[0]
        req = make_request(cookie=cookie, method="GET")

        all_dash = await app.api_dashboard(req)
        self.assertEqual(all_dash["revenue"], 500_000)
        branch_dash = await app.api_dashboard(req, store_id=branch["id"])
        self.assertEqual(branch_dash["revenue"], 200_000)
        self.assertEqual(branch_dash["product_count"], 1)
        main_sales = await app.api_sales(req, store_id=bot.main_store_id())
        self.assertEqual(main_sales["revenue"], 300_000)

    async def test_panel_settlement_and_debts(self):
        bot.create_user("admin2", "parol123", "Boss", "admin")
        branch = bot.branch_store()
        seller = bot.create_user("sotuvchi2", "parol123", "Vali", "seller", branch["id"])
        bot.inventory_create(
            "Aziya", "dona", 1, 100_000, 200_000,
            store_id=branch["id"], item_numbers=["P-1"],
        )
        bot.sell_branch_item(branch["id"], "P-1", user_id=seller["id"])
        login = await app.api_login(
            make_request({"login": "admin2", "password": "parol123"})
        )
        cookie = login.headers["set-cookie"].split(";")[0]

        overview = await app.api_settlements(make_request(cookie=cookie, method="GET"))
        self.assertEqual(overview["rows"][0]["sales_total"], 200_000)

        result = await app.api_create_settlement(
            make_request(
                {"store_id": branch["id"], "seller_id": seller["id"],
                 "received_amount": 120_000},
                cookie=cookie,
            )
        )
        self.assertEqual(result["debt_amount"], 80_000)
        debts = await app.api_debts(make_request(cookie=cookie, method="GET"))
        self.assertEqual(len(debts), 1)
        self.assertEqual(debts[0]["remaining"], 80_000)
        self.assertEqual(debts[0]["seller_name"], "Vali")


if __name__ == "__main__":
    unittest.main()
