import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from fastapi import HTTPException
from starlette.requests import Request

import app
import bot


def make_request(body=None, cookie=None, method="POST", authorization=None):
    headers = [(b"content-type", b"application/json")]
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    if authorization:
        headers.append((b"authorization", authorization.encode()))
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


def make_init_data(token, telegram_id):
    values = {
        "auth_date": str(int(time.time())),
        "query_id": "AAE-test-query",
        "user": json.dumps(
            {"id": telegram_id, "first_name": "Seller"},
            separators=(",", ":"),
        ),
    }
    check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(values.items())
    )
    secret_key = hmac.new(
        b"WebAppData",
        token.encode(),
        hashlib.sha256,
    ).digest()
    values["hash"] = hmac.new(
        secret_key,
        check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(values)


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

    async def test_api_delete_user_requires_admin_and_protects_admin(self):
        admin = bot.create_user("admin1", "parol123", "Boss", "admin")
        seller = bot.create_user("sotuvchi", "parol123", "Ali", "seller")
        seller_actor = bot.create_user("sotuvchi2", "parol123", "Vali", "seller")

        seller_login = await app.api_login(
            make_request({"login": seller_actor["login"], "password": "parol123"})
        )
        seller_request = make_request(
            cookie=seller_login.headers["set-cookie"].split(";")[0],
            method="DELETE",
        )
        with self.assertRaises(HTTPException) as raised:
            await app.api_delete_user(seller["id"], seller_request)
        self.assertEqual(raised.exception.status_code, 403)

        admin_login = await app.api_login(
            make_request({"login": admin["login"], "password": "parol123"})
        )
        admin_request = make_request(
            cookie=admin_login.headers["set-cookie"].split(";")[0],
            method="DELETE",
        )
        with self.assertRaises(HTTPException) as raised:
            await app.api_delete_user(admin["id"], admin_request)
        self.assertEqual(raised.exception.status_code, 403)
        self.assertIsNotNone(bot.get_user(admin["id"]))

        result = await app.api_delete_user(seller["id"], admin_request)
        self.assertEqual(result, {"ok": True})
        self.assertIsNone(bot.get_user(seller["id"]))

    async def test_seller_panel_is_store_scoped_and_hides_admin_data(self):
        branch = bot.branch_store()
        seller = bot.create_user(
            "panel-seller",
            "parol123",
            "Sotuvchi",
            "seller",
            branch["id"],
        )
        bot.inventory_create(
            "SellerPrivatePrice",
            "dona",
            2,
            100_000,
            200_000,
            store_id=branch["id"],
            item_numbers=["PANEL-001", "PANEL-002"],
        )
        login = await app.api_login(
            make_request({"login": seller["login"], "password": "parol123"})
        )
        cookie = login.headers["set-cookie"].split(";")[0]
        seller_request = make_request(cookie=cookie, method="GET")

        products = await app.api_products(seller_request)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["name"], "SellerPrivatePrice")
        # Sotuvchi o'z do'koni narxlarini ko'radi (kelish/sotish tahrirlash uchun)
        self.assertEqual(products[0]["sale_price"], 200_000)
        self.assertEqual(products[0]["purchase_price"], 100_000)
        items = await app.api_items(seller_request)
        self.assertEqual({item["number"] for item in items}, {"PANEL-001", "PANEL-002"})
        self.assertIn("inventory_id", items[0])
        self.assertNotIn("sale_price", items[0])
        self.assertNotIn("purchase_price", items[0])

        sale = await app.api_branch_sell(
            make_request(
                {"number": "PANEL-001", "store_id": bot.main_store_id()},
                cookie=cookie,
            )
        )
        self.assertTrue(sale["ok"])
        self.assertNotIn("sale", sale)
        self.assertEqual(
            set(sale["item"]),
            {"number", "product_name", "status"},
        )
        today = await app.api_my_today_sales(seller_request)
        self.assertEqual(today["sales_count"], 1)
        self.assertEqual(today["revenue"], 200_000)

        with self.assertRaises(HTTPException) as raised:
            await app.api_users(seller_request)
        self.assertEqual(raised.exception.status_code, 403)
        with self.assertRaises(HTTPException) as raised:
            await app.api_sales(seller_request)
        self.assertEqual(raised.exception.status_code, 403)

        with self.assertRaises(HTTPException) as raised:
            await app.api_create_product(
                make_request(
                    {"name": "NoLabelProduct", "quantity": 10},
                    cookie=cookie,
                )
            )
        self.assertEqual(raised.exception.status_code, 422)

        created = await app.api_create_product(
            make_request(
                {
                    "name": "SellerLabeledProduct",
                    "unit": "dona",
                    "quantity": 99,
                    "purchase_price": 110_000,
                    "sale_price": 210_000,
                    "store_id": bot.main_store_id(),
                    "item_numbers": ["PANEL-003"],
                },
                cookie=cookie,
            )
        )
        self.assertEqual(created["name"], "SellerLabeledProduct")
        self.assertEqual(created["quantity"], 1)
        self.assertEqual(created["sale_price"], 210_000)
        self.assertEqual(
            bot.inventory_get(created["id"])["store_id"],
            branch["id"],
        )
        restocked = await app.api_branch_restock(
            make_request(
                {
                    "inventory_id": created["id"],
                    "numbers": ["PANEL-004"],
                },
                cookie=cookie,
            )
        )
        self.assertEqual(restocked["quantity"], 2)

    async def test_telegram_webapp_auth_resolves_linked_seller_and_store(self):
        token = "123456:local-test-token"
        branch = bot.branch_store()
        seller = bot.create_user(
            "tma-seller",
            "parol123",
            "Web Sotuvchi",
            "seller",
            branch["id"],
        )
        bot.link_user_telegram(seller["id"], 7654321)
        bot.inventory_create(
            "TmaBranchCarpet",
            "dona",
            1,
            100,
            200,
            store_id=branch["id"],
        )
        bot.inventory_create("TmaMainCarpet", "dona", 1, 100, 200)
        authorization = f"tma {make_init_data(token, 7654321)}"
        request = make_request(method="GET", authorization=authorization)

        with patch.object(app, "BOT_TOKEN", token), patch.dict(
            os.environ,
            {"TELEGRAM_ADMIN_ID": "910471611"},
        ):
            panel_user = app.require_panel_user(request)
            self.assertEqual(panel_user["id"], seller["id"])
            self.assertEqual(panel_user["store_id"], branch["id"])
            stores = await app.api_stores(request)
            self.assertEqual([store["id"] for store in stores], [branch["id"]])
            products = await app.api_products(request)
            self.assertEqual(
                [product["name"] for product in products],
                ["TmaBranchCarpet"],
            )

            with self.assertRaises(HTTPException) as raised:
                await app.api_users(request)
            self.assertEqual(raised.exception.status_code, 403)

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
