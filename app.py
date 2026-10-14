import base64
import hmac
import json
import os
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from hashlib import sha256
from ipaddress import ip_address
from typing import Any, Literal, overload
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from telegram import Update

import bot
from bot import BOT_TOKEN, TELEGRAM_ADMIN_ID, WEBHOOK_SECRET, build_application

telegram_application = None
PANEL_PATH = bot.BASE_DIR / "static" / "index.html"
SHOP_TIMEZONE = ZoneInfo("Asia/Tashkent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_application

    if bot.LOCAL_WEBAPP_DEV and not os.getenv("VERCEL"):
        yield
        return

    # Panel API (dashboard/products/sales) Telegram app siz ham ishlashi kerak.
    # Shuning uchun Telegram init xatosi panelni yiqitmasligi shart —
    # faqat /webhook 503 qaytaradi, panel esa DB bilan ishlayveradi.
    try:
        if not WEBHOOK_SECRET:
            print("WARNING: WEBHOOK_SECRET missing — webhook disabled, panel still works")
        else:
            telegram_application = build_application()
            await telegram_application.initialize()
            await bot.configure_web_app_menu(telegram_application)
            if os.getenv("VERCEL_ENV") == "production":
                production_url = os.getenv("VERCEL_PROJECT_PRODUCTION_URL", "").strip()
                if production_url:
                    await telegram_application.bot.set_webhook(
                        url=f"https://{production_url}/webhook",
                        secret_token=WEBHOOK_SECRET,
                    )
                else:
                    print("WARNING: VERCEL_PROJECT_PRODUCTION_URL missing — webhook not set")
            await telegram_application.start()
            app.state.telegram_application = telegram_application
    except Exception as exc:
        import traceback
        print(f"WARNING: Telegram init failed, panel continues without webhook: {exc!r}")
        traceback.print_exc()
        telegram_application = None
    try:
        yield
    finally:
        if telegram_application is not None:
            try:
                await telegram_application.stop()
                await telegram_application.shutdown()
            except Exception:
                pass
            telegram_application = None


app = FastAPI(lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    import traceback
    if isinstance(exc, HTTPException):
        raise exc
    # Vercel Runtime Logs da ko'rinishi uchun traceback ni stdout ga chiqaramiz.
    # Foydalanuvchiga ichki tafsilotni oshkor qilmaymiz.
    print(f"Unhandled error on {request.method} {request.url.path}: {exc!r}")
    traceback.print_exc()
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=500, content={"detail": "Ichki server xatosi"})


def verify_telegram_init_data(init_data: str, bot_token: str, admin_id: str):
    if not admin_id.isdigit():
        raise HTTPException(status_code=503, detail="Web panel admin configuration is missing")
    user = verify_telegram_webapp_data(init_data, bot_token)
    if int(user["id"]) != int(admin_id):
        raise HTTPException(status_code=403, detail="This Telegram account is not an admin")
    return user


def verify_telegram_webapp_data(init_data: str, bot_token: str):
    if not init_data or not bot_token:
        raise HTTPException(status_code=503, detail="Telegram bot configuration is missing")
    values = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = values.pop("hash", "")
    if not received_hash:
        raise HTTPException(status_code=401, detail="Telegram authorization data is missing")

    data_check_string = "\n".join(
        f"{key}={value}" for key, value in sorted(values.items())
    )
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode(),
        sha256,
    ).digest()
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        sha256,
    ).hexdigest()
    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram authorization data")

    try:
        auth_date = int(values["auth_date"])
        user = json.loads(values["user"])
        user_id = int(user["id"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=401, detail="Malformed Telegram authorization data") from error

    current_time = int(datetime.now().timestamp())
    if auth_date > current_time + 60 or current_time - auth_date > 86400:
        raise HTTPException(status_code=401, detail="Telegram authorization data has expired")
    return user


# --- Login/password sessions (panel works outside Telegram too) -------------

def _session_secret() -> str:
    return WEBHOOK_SECRET or BOT_TOKEN or "glam-local-dev-secret"


def make_session_token(user_id: int) -> str:
    payload = {"uid": user_id, "exp": int(time.time()) + 7 * 24 * 3600}
    body = base64.urlsafe_b64encode(
        json.dumps(payload).encode()
    ).decode().rstrip("=")
    signature = hmac.new(
        _session_secret().encode(), body.encode(), sha256
    ).hexdigest()
    return f"{body}.{signature}"


def parse_session_token(token: str):
    try:
        body, signature = token.rsplit(".", 1)
        expected = hmac.new(
            _session_secret().encode(), body.encode(), sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(
            base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        )
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def panel_user(request: Request):
    """The users-table account behind a valid session cookie, if any."""
    token = request.cookies.get("glam_session")
    if not token:
        return None
    payload = parse_session_token(token)
    if not payload:
        return None
    user = bot.get_user(int(payload["uid"]))
    if user and user["is_active"]:
        return user
    return None


def require_panel_user(request: Request):
    """Any panel account: session user or a linked Telegram account."""
    user = panel_user(request)
    if user:
        return user
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("tma "):
        telegram_user = verify_telegram_webapp_data(
            authorization[4:],
            BOT_TOKEN,
        )
        telegram_id = int(telegram_user["id"])
        if current_admin_id().isdigit() and telegram_id == int(current_admin_id()):
            return {
                "id": None,
                "login": "admin",
                "display_name": telegram_user.get("first_name") or "Admin",
                "role": "admin",
                "store_id": None,
            }
        account = bot.get_user_by_telegram(telegram_id)
        if account:
            return account
        raise HTTPException(
            status_code=403,
            detail="Avval botda login/parol bilan kiring va profilni Telegram’ga ulang",
        )
    telegram_admin = require_admin(request)
    return {
        "id": None,
        "login": "admin",
        "display_name": telegram_admin.get("first_name") or "Admin",
        "role": "admin",
        "store_id": None,
    }


def require_seller_store(user: dict) -> int:
    store_id = user.get("store_id")
    if store_id is None:
        raise HTTPException(
            status_code=403,
            detail="Hisobingizga do‘kon biriktirilmagan. Administratorga murojaat qiling.",
        )
    return int(store_id)


def current_admin_id() -> str:
    # Read the current config at call time (like bot.LOCAL_WEBAPP_DEV is read
    # via the bot module), so a TELEGRAM_ADMIN_ID set in the environment after
    # import — e.g. by the local-dev launcher — is honoured. Falls back to the
    # value bot loaded from .env at import time.
    return os.getenv("TELEGRAM_ADMIN_ID", "").strip() or TELEGRAM_ADMIN_ID


def require_admin(request: Request):
    session_user = panel_user(request)
    if session_user:
        if session_user["role"] != "admin":
            raise HTTPException(status_code=403, detail="Admin huquqi kerak")
        return {
            "id": session_user["id"],
            "first_name": session_user.get("display_name") or session_user["login"],
            "app_user": session_user,
        }
    authorization = request.headers.get("Authorization", "")
    client_host = request.client.host if request.client else ""
    try:
        is_loopback = bool(client_host and ip_address(client_host).is_loopback)
    except ValueError:
        is_loopback = False
    if (
        bot.LOCAL_WEBAPP_DEV
        and not os.getenv("VERCEL")
        and is_loopback
        and authorization == "local-dev"
    ):
        admin_id = current_admin_id()
        if not admin_id.isdigit():
            raise HTTPException(
                status_code=503,
                detail="Set TELEGRAM_ADMIN_ID in .env for local panel testing",
            )
        return {"id": int(admin_id), "first_name": "Local test"}
    if not authorization.startswith("tma "):
        raise HTTPException(status_code=401, detail="Open this panel inside Telegram")
    return verify_telegram_init_data(
        authorization[4:],
        BOT_TOKEN,
        current_admin_id(),
    )


def parse_day(value: str | None, fallback: date) -> date:
    if not value:
        return fallback
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Date must use YYYY-MM-DD format") from error


def period_bounds(start: str | None, end: str | None):
    today = datetime.now(SHOP_TIMEZONE).date()
    first_day = parse_day(start, today)
    last_day = parse_day(end, today)
    if first_day > last_day:
        raise HTTPException(status_code=422, detail="Start date must not follow end date")
    start_at = datetime.combine(first_day, datetime.min.time(), SHOP_TIMEZONE).isoformat()
    end_at = datetime.combine(
        last_day + timedelta(days=1),
        datetime.min.time(),
        SHOP_TIMEZONE,
    ).isoformat()
    return first_day.isoformat(), last_day.isoformat(), start_at, end_at


@overload
def as_number(
    value: Any,
    field: str,
    *,
    nullable: Literal[False] = False,
) -> float: ...


@overload
def as_number(
    value: Any,
    field: str,
    *,
    nullable: Literal[True],
) -> float | None: ...


def as_number(
    value: Any,
    field: str,
    *,
    nullable: bool = False,
) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if nullable:
            return None
        raise HTTPException(status_code=422, detail=f"{field} must be a number")
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise HTTPException(status_code=422, detail=f"{field} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=f"{field} must be a number") from error
    if result < 0 or not (result < float("inf")):
        raise HTTPException(status_code=422, detail=f"{field} must be a non-negative finite number")
    return result


async def json_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Request body must be valid JSON") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object")
    return payload


def row_dict(row):
    return dict(row) if row is not None else None


def product_dict(row, rate: float | None = None):
    item = row_dict(row)
    if item is not None:
        item.pop("article", None)
        item.pop("name_key", None)
        if rate is None:
            # Callers rendering many rows pass one pre-fetched rate so a
            # request does not open a DB connection per product.
            rate = bot.get_usd_rate()
        if item.get("purchase_price_usd") is not None:
            item["purchase_price_usd"] = float(item["purchase_price_usd"])
        elif item.get("purchase_price") is not None and rate > 0:
            item["purchase_price_usd"] = round(float(item["purchase_price"]) / rate, 2)
        if item.get("total_profit") is not None:
            item["total_profit"] = float(item["total_profit"])
            item["total_profit_usd"] = round(item["total_profit"] / rate, 2) if rate > 0 else 0.0
        else:
            item["total_profit"] = 0.0
            item["total_profit_usd"] = 0.0
    return item


def seller_product_dict(row):
    item = row_dict(row)
    if item is None:
        return None
    # Sotuvchi o'z do'konida narxlarni ko'rishi va tahrirlashi kerak.
    return {
        key: item.get(key)
        for key in ("id", "name", "unit", "quantity", "archived_at",
                    "dimensions", "area_sqm", "sale_price", "purchase_price",
                    "price_per_sqm_usd", "purchase_price_usd", "store_id")
    }


def movement_dict(row):
    item = row_dict(row)
    if item is not None:
        item.pop("article_snapshot", None)
    return item


def serialize_sale(row, rate: float | None = None):
    item = row_dict(row)
    if item is None:
        return None
    item.pop("article_snapshot", None)
    for key in ("quantity", "purchase_price", "sale_price"):
        if item.get(key) is not None:
            item[key] = float(item[key])
    if rate is None:
        rate = bot.get_usd_rate()
    if item.get("purchase_price_usd") is not None:
        item["purchase_price_usd"] = float(item["purchase_price_usd"])
    elif item.get("purchase_price") is not None and rate > 0:
        item["purchase_price_usd"] = round(item["purchase_price"] / rate, 2)

    item["profit"] = (
        (item["sale_price"] - item["purchase_price"]) * item["quantity"]
        if item["sale_price"] is not None and item["purchase_price"] is not None
        else None
    )
    item["profit_usd"] = (
        round(item["profit"] / rate, 2)
        if item["profit"] is not None and rate > 0
        else None
    )
    item["sale_price_usd"] = (
        round(item["sale_price"] / rate, 2)
        if item["sale_price"] is not None and rate > 0
        else None
    )
    return item


def aggregate_sales(rows, rate: float | None = None):
    if rate is None:
        rate = bot.get_usd_rate()
    sales = [serialize_sale(row, rate) for row in rows]
    sales = [sale for sale in sales if sale is not None]
    revenue = sum(
        sale["sale_price"] * sale["quantity"]
        for sale in sales
        if sale["sale_price"] is not None
    )
    profit = sum(sale["profit"] for sale in sales if sale["profit"] is not None)
    return {
        "sales_count": len(sales),
        "quantity": sum(sale["quantity"] for sale in sales),
        "revenue": revenue,
        "revenue_usd": round(revenue / rate, 2) if rate > 0 else 0.0,
        "profit": profit,
        "profit_usd": round(profit / rate, 2) if rate > 0 else 0.0,
        "unpriced_count": sum(1 for sale in sales if sale["profit"] is None),
        "usd_rate": rate,
        "sales": sales,
    }


@app.get("/")
async def health():
    return {"status": "ok"}


@app.get("/panel")
async def web_panel():
    return FileResponse(PANEL_PATH)


@app.get("/api/settings")
async def api_get_settings(request: Request):
    require_admin(request)
    return {"usd_rate": bot.get_usd_rate()}


@app.post("/api/settings")
async def api_set_settings(request: Request):
    require_admin(request)
    payload = await json_payload(request)
    if "usd_rate" in payload:
        rate = as_number(payload["usd_rate"], "usd_rate")
        if rate <= 0:
            raise HTTPException(status_code=422, detail="usd_rate must be positive")
        bot.set_usd_rate(rate)
    return {"usd_rate": bot.get_usd_rate()}


@app.get("/api/bootstrap")
async def api_bootstrap(request: Request):
    admin = require_admin(request)
    today = datetime.now(SHOP_TIMEZONE).date().isoformat()
    rate = bot.get_usd_rate()
    return {
        "admin": {
            "id": admin["id"],
            "name": " ".join(
                part for part in (admin.get("first_name"), admin.get("last_name")) if part
            ),
        },
        "today": today,
        "usd_rate": rate,
        "products": [
            product_dict(row, rate)
            for row in bot.inventory_list_full(include_archived=True, limit=1000)
        ],
    }


@app.get("/api/dashboard")
async def api_dashboard(request: Request, start: str | None = None, end: str | None = None, store_id: int | None = None):
    require_admin(request)
    first_day, last_day, start_at, end_at = period_bounds(start, end)
    rate = bot.get_usd_rate()
    sales_rows = bot.sales_list(start_at, end_at, limit=10000)
    if store_id is not None:
        main_id = bot.main_store_id()
        filtered = []
        for row in sales_rows:
            try:
                sid = row["store_id"] if "store_id" in dict(row) else None
            except Exception:
                sid = None
            if (sid if sid is not None else main_id) == store_id:
                filtered.append(row)
        sales_rows = filtered
    aggregate = aggregate_sales(sales_rows, rate)
    products = bot.inventory_list_full(limit=1000, store_id=store_id)
    aggregate.update(
        {
            "start": first_day,
            "end": last_day,
            "product_count": len(products),
            "stock_quantity": sum(float(item["quantity"]) for item in products),
            "products": [product_dict(item, rate) for item in products],
        }
    )
    return aggregate


@app.get("/api/products")
async def api_products(request: Request, archived: bool = False, store_id: int | None = None):
    user = require_panel_user(request)
    if user["role"] == "seller":
        store_id = require_seller_store(user)
        archived = False
        return [
            seller_product_dict(row)
            for row in bot.inventory_list_full(
                include_archived=archived,
                limit=1000,
                store_id=store_id,
            )
        ]
    rate = bot.get_usd_rate()
    return [
        product_dict(row, rate)
        for row in bot.inventory_list_full(
            include_archived=archived,
            limit=1000,
            store_id=store_id,
        )
    ]


@app.get("/api/main-product-names")
async def api_main_product_names(request: Request):
    require_panel_user(request)
    main_store_id = bot.main_store_id()
    return [
        str(product["name"])
        for product in bot.inventory_list_full(
            include_archived=False,
            limit=1000,
            store_id=main_store_id,
        )
    ]


@app.get("/api/products/export")
async def api_products_export(request: Request, archived: bool = True):
    require_admin(request)
    csv_bytes = bot.generate_inventory_csv_bytes(include_archived=archived)
    now_str = datetime.now(SHOP_TIMEZONE).strftime("%Y-%m-%d")
    filename = f"qamashi-gilam-ombor-{now_str}.csv"
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/products")
async def api_create_product(request: Request):
    user = require_panel_user(request)
    payload = await json_payload(request)
    name = str(payload.get("name", "")).strip()
    unit = str(payload.get("unit", "dona")).strip().lower()
    if not name or unit not in {"dona", "metr"}:
        raise HTTPException(status_code=422, detail="Name and valid unit are required")
    dimensions = str(payload.get("dimensions", "")).strip() or None
    area_sqm = as_number(payload.get("area_sqm"), "area_sqm", nullable=True)
    price_per_sqm_usd = as_number(payload.get("price_per_sqm_usd"), "price_per_sqm_usd", nullable=True)
    purchase_price_usd = as_number(payload.get("purchase_price_usd"), "purchase_price_usd", nullable=True)
    store_id = payload.get("store_id")
    if user["role"] == "seller":
        store_id = require_seller_store(user)
    item_numbers = payload.get("item_numbers") or None
    if isinstance(item_numbers, str):
        item_numbers = [n for n in item_numbers.replace("\n", ",").split(",") if n.strip()]
    if user["role"] == "seller":
        if unit != "dona" or not item_numbers:
            raise HTTPException(
                status_code=422,
                detail="Sotuvchi gilamni faqat yorliq raqamlari bilan qo‘sha oladi",
            )
    quantity = as_number(payload.get("quantity", 0), "quantity")
    if item_numbers:
        quantity = float(len(item_numbers))
    sale_price = as_number(payload.get("sale_price"), "sale_price", nullable=True)
    if sale_price is None:
        raise HTTPException(
            status_code=422,
            detail="Sotish narxini kiritish shart — narxsiz gilam qo'shib bo'lmaydi",
        )
    try:
        product = bot.inventory_create(
            name,
            unit,
            quantity,
            as_number(payload.get("purchase_price"), "purchase_price", nullable=True),
            sale_price,
            dimensions=dimensions,
            area_sqm=area_sqm,
            price_per_sqm_usd=price_per_sqm_usd,
            purchase_price_usd=purchase_price_usd,
            store_id=int(store_id) if store_id else None,
            item_numbers=item_numbers,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        if "UNIQUE" in str(error).upper() or "DUPLICATE KEY" in str(error).upper():
            raise HTTPException(
                status_code=409,
                detail="Bu gilam nomi bazada bor. Mavjud gilamni tahrirlang.",
            ) from error
        raise
    return (
        seller_product_dict(
            {**dict(product), **dict(bot.current_product_prices(int(product["id"])))}
        )
        if user["role"] == "seller"
        else product_dict(product)
    )


@app.patch("/api/products/{product_id}")
async def api_edit_product(product_id: int, request: Request):
    user = require_panel_user(request)
    payload = await json_payload(request)
    current = bot.inventory_get(product_id)
    if not current:
        raise HTTPException(status_code=404, detail="Product not found")
    current = dict(current)
    if user["role"] == "seller":
        own = require_seller_store(user)
        if int(current.get("store_id") or 0) != int(own):
            raise HTTPException(status_code=403, detail="Bu sizning do'koningiz emas")
        # Sotuvchi: nom, o'lcham va narxlarni tahrirlashi mumkin.
        # Miqdor raqamlar orqali, birlik esa o'zgarmaydi.
        name = str(payload.get("name", current.get("name") or "")).strip()
        if not name:
            raise HTTPException(status_code=422, detail="Name and valid unit are required")
        unit = current.get("unit") or "dona"
        existing_prices = dict(bot.current_product_prices(product_id))
        quantity = current.get("quantity")
        purchase_price = as_number(
            payload.get("purchase_price", existing_prices.get("purchase_price")),
            "purchase_price",
            nullable=True,
        )
        sale_price = as_number(
            payload.get("sale_price", existing_prices.get("sale_price")),
            "sale_price",
            nullable=True,
        )
        dimensions = (
            str(payload["dimensions"]).strip()
            if "dimensions" in payload and payload["dimensions"] is not None
            else current.get("dimensions")
        )
        area_sqm = as_number(
            payload.get("area_sqm", current.get("area_sqm")),
            "area_sqm",
            nullable=True,
        )
        price_per_sqm_usd = as_number(
            payload.get("price_per_sqm_usd", existing_prices.get("price_per_sqm_usd")),
            "price_per_sqm_usd",
            nullable=True,
        )
        purchase_price_usd = as_number(
            payload.get("purchase_price_usd", existing_prices.get("purchase_price_usd")),
            "purchase_price_usd",
            nullable=True,
        )
        try:
            product, status = bot.inventory_edit(
                product_id,
                name,
                unit,
                quantity,
                purchase_price,
                sale_price,
                dimensions=dimensions,
                area_sqm=area_sqm,
                price_per_sqm_usd=price_per_sqm_usd,
                purchase_price_usd=purchase_price_usd,
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        if status == "name_exists":
            raise HTTPException(
                status_code=409,
                detail="Bazada shu nomli boshqa gilam bor. Boshqa nom tanlang.",
            )
        if status == "not_found":
            raise HTTPException(status_code=404, detail="Product not found")
        return seller_product_dict(product)
    name = str(payload.get("name", "")).strip()
    unit = str(payload.get("unit", "dona")).strip().lower()
    if not name or unit not in {"dona", "metr"}:
        raise HTTPException(status_code=422, detail="Name and valid unit are required")
    existing_prices = dict(bot.current_product_prices(product_id))
    quantity = as_number(payload.get("quantity", current["quantity"]), "quantity")
    purchase_price = as_number(
        payload.get("purchase_price", existing_prices["purchase_price"]),
        "purchase_price",
        nullable=True,
    )
    sale_price = as_number(
        payload.get("sale_price", existing_prices["sale_price"]),
        "sale_price",
        nullable=True,
    )
    dimensions = (
        str(payload["dimensions"]).strip()
        if "dimensions" in payload and payload["dimensions"] is not None
        else current.get("dimensions")
    )
    area_sqm = as_number(
        payload.get("area_sqm", current.get("area_sqm")),
        "area_sqm",
        nullable=True,
    )
    price_per_sqm_usd = as_number(
        payload.get("price_per_sqm_usd", existing_prices.get("price_per_sqm_usd")),
        "price_per_sqm_usd",
        nullable=True,
    )
    purchase_price_usd = as_number(
        payload.get("purchase_price_usd", existing_prices.get("purchase_price_usd")),
        "purchase_price_usd",
        nullable=True,
    )
    try:
        product, status = bot.inventory_edit(
            product_id,
            name,
            unit,
            quantity,
            purchase_price,
            sale_price,
            dimensions=dimensions,
            area_sqm=area_sqm,
            price_per_sqm_usd=price_per_sqm_usd,
            purchase_price_usd=purchase_price_usd,
        )
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if status == "name_exists":
        raise HTTPException(
            status_code=409,
            detail="Bazada shu nomli boshqa gilam bor. Boshqa nom tanlang.",
        )
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Product not found")
    return product_dict(product)


@app.delete("/api/products/{product_id}")
async def api_archive_or_delete_product(
    product_id: int,
    request: Request,
    permanent: bool = False,
):
    require_admin(request)
    if permanent:
        if not bot.inventory_delete(product_id):
            raise HTTPException(status_code=404, detail="Product not found")
        return {"ok": True, "deleted": True}
    if not bot.inventory_archive(product_id):
        raise HTTPException(status_code=404, detail="Product not found")
    return {"ok": True, "archived": True}


@app.delete("/api/products/{product_id}/permanent")
async def api_delete_product_permanent(product_id: int, request: Request):
    require_admin(request)
    if not bot.inventory_delete(product_id):
        raise HTTPException(status_code=404, detail="Product not found")
    return {"ok": True, "deleted": True}


@app.post("/api/products/{product_id}/restore")
async def api_restore_product(product_id: int, request: Request):
    require_admin(request)
    if not bot.inventory_restore(product_id):
        raise HTTPException(status_code=404, detail="Product not found")
    return {"ok": True, "archived": False}


@app.get("/api/products/{product_id}/stats")
async def api_product_stats(
    product_id: int,
    request: Request,
    start: str | None = None,
    end: str | None = None,
):
    require_admin(request)
    product = bot.inventory_get(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    first_day, last_day, start_at, end_at = period_bounds(start, end)
    stats = aggregate_sales(
        [
            row
            for row in bot.sales_list(start_at, end_at, limit=10000)
            if int(row["inventory_id"]) == product_id
        ]
    )
    stats.update(
        {
            "product": product_dict(product, stats["usd_rate"]),
            "start": first_day,
            "end": last_day,
            "price_history": [
                row_dict(row) for row in bot.product_price_history(product_id)
            ],
            "movement_history": [
                movement_dict(row)
                for row in bot.movement_history(product_id, limit=200)
            ],
        }
    )
    return stats


@app.get("/api/sales")
async def api_sales(
    request: Request,
    start: str | None = None,
    end: str | None = None,
    store_id: int | None = None,
):
    require_admin(request)
    first_day, last_day, start_at, end_at = period_bounds(start, end)
    sales_rows = bot.sales_list(start_at, end_at, limit=1000)
    if store_id is not None:
        main_id = bot.main_store_id()
        filtered = []
        for row in sales_rows:
            try:
                sid = row["store_id"] if "store_id" in dict(row) else None
            except Exception:
                sid = None
            if (sid if sid is not None else main_id) == store_id:
                filtered.append(row)
        sales_rows = filtered
    aggregate = aggregate_sales(sales_rows)
    aggregate.update({"start": first_day, "end": last_day})
    return aggregate


@app.patch("/api/sales/{movement_id}")
async def api_edit_sale(movement_id: int, request: Request):
    require_admin(request)
    payload = await json_payload(request)
    quantity = as_number(payload.get("quantity"), "quantity")
    if quantity <= 0:
        raise HTTPException(status_code=422, detail="Sale quantity must be greater than zero")
    sale_price = as_number(
        payload.get("sale_price"),
        "sale_price",
        nullable=True,
    )
    created_at = str(payload.get("created_at", ""))
    try:
        if len(created_at) == 10:
            created_at += "T12:00:00+05:00"
        parsed_at = datetime.fromisoformat(created_at)
        if parsed_at.tzinfo is None:
            parsed_at = parsed_at.replace(tzinfo=SHOP_TIMEZONE)
        created_at = parsed_at.astimezone(SHOP_TIMEZONE).isoformat()
        updated, status = bot.inventory_update_sale(
            movement_id,
            quantity,
            sale_price,
            created_at,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Invalid sale date or stock quantity") from error
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Sale not found")
    return updated


@app.delete("/api/sales/{movement_id}")
async def api_delete_sale(movement_id: int, request: Request):
    require_admin(request)
    stock, status = bot.inventory_delete_sale(movement_id)
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Sale not found")
    return {"ok": True, "returned_to_stock": stock}


# --- Multi-store panel API: auth, stores, branch, users, settlements --------

@app.post("/api/login")
async def api_login(request: Request):
    payload = await json_payload(request)
    user, error = bot.authenticate_user(
        str(payload.get("login", "")), str(payload.get("password", ""))
    )
    if not user:
        raise HTTPException(status_code=401, detail=error or "Login yoki parol noto'g'ri")
    response = Response(
        content=json.dumps({"user": user}),
        media_type="application/json",
    )
    response.set_cookie(
        "glam_session",
        make_session_token(int(user["id"])),
        max_age=7 * 24 * 3600,
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/api/logout")
async def api_logout():
    response = Response(content=json.dumps({"ok": True}), media_type="application/json")
    response.delete_cookie("glam_session")
    return response


@app.get("/api/me")
async def api_me(request: Request):
    user = require_panel_user(request)
    return {
        "id": user.get("id"),
        "login": user.get("login"),
        "display_name": user.get("display_name"),
        "role": user.get("role"),
        "store_id": user.get("store_id"),
    }


@app.get("/api/stores")
async def api_stores(request: Request):
    user = require_panel_user(request)
    stores = [dict(row) for row in bot.list_stores()]
    if user["role"] == "seller":
        own = require_seller_store(user)
        stores = [s for s in stores if s["id"] == own]
    return stores


@app.get("/api/items")
async def api_items(request: Request, store_id: int | None = None):
    user = require_panel_user(request)
    if user["role"] == "seller":
        store_id = require_seller_store(user)
    if store_id is None:
        branch = bot.branch_store()
        store_id = branch["id"] if branch else bot.main_store_id()
    rows = bot.list_items(store_id)
    if user["role"] == "seller":
        return [
            {
                key: item[key]
                for key in (
                    "id",
                    "inventory_id",
                    "number",
                    "product_name",
                    "status",
                    "created_at",
                    "sold_at",
                )
                if key in item
            }
            for item in (dict(row) for row in rows)
        ]
    return [dict(row) for row in rows]


@app.get("/api/my/sales/today")
async def api_my_today_sales(request: Request):
    user = require_panel_user(request)
    if user["role"] != "seller":
        raise HTTPException(status_code=403, detail="Bu bo‘lim faqat sotuvchilar uchun")
    store_id = require_seller_store(user)
    today = datetime.now(SHOP_TIMEZONE).date().isoformat()
    summary = bot.store_day_sales(store_id, today, user["id"])
    return {
        "date": today,
        "sales_count": summary["sales_count"],
        "revenue": summary["revenue"],
    }


@app.post("/api/branch/sell")
async def api_branch_sell(request: Request):
    user = require_panel_user(request)
    payload = await json_payload(request)
    number = str(payload.get("number", "")).strip()
    if not number:
        raise HTTPException(status_code=422, detail="Raqam kerak")
    store_id = payload.get("store_id")
    if user["role"] == "seller":
        store_id = require_seller_store(user)
    if store_id is None:
        branch = bot.branch_store()
        store_id = branch["id"] if branch else bot.main_store_id()
    sale_price = as_number(payload.get("sale_price"), "sale_price", nullable=True)
    item, status, sale = bot.sell_branch_item(
        int(store_id), number, user_id=user.get("id"), sale_price=sale_price
    )
    if status == "not_found":
        raise HTTPException(status_code=404, detail="Bu raqamli gilam topilmadi")
    if status == "already_sold":
        raise HTTPException(status_code=409, detail="Bu gilam allaqachon sotilgan")
    if status != "ok":
        raise HTTPException(status_code=409, detail=f"Sotuv amalga oshmadi: {status}")
    refreshed = bot.get_item_by_number(int(store_id), number)
    if user["role"] == "seller":
        item_data = dict(refreshed or item)
        return {
            "ok": True,
            "item": {
                key: item_data[key]
                for key in ("number", "product_name", "status")
                if key in item_data
            },
        }
    return {"ok": True, "item": dict(refreshed or item), "sale": sale}


@app.patch("/api/items/{item_id}")
async def api_rename_item(item_id: int, request: Request):
    user = require_panel_user(request)
    payload = await json_payload(request)
    new_number = str(payload.get("number", "")).strip()
    if not new_number:
        raise HTTPException(status_code=422, detail="Yangi raqam kerak")
    conn = bot.db()
    try:
        row = conn.execute(
            "SELECT * FROM inventory_items WHERE id=?", (item_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Raqam topilmadi")
    row = dict(row)
    if user["role"] == "seller" and int(row["store_id"]) != require_seller_store(user):
        raise HTTPException(status_code=403, detail="Bu sizning do'koningiz emas")
    try:
        updated = bot.rename_item_number(int(row["store_id"]), row["number"], new_number)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return dict(updated)


@app.delete("/api/items/{item_id}")
async def api_delete_item(item_id: int, request: Request):
    user = require_panel_user(request)
    conn = bot.db()
    try:
        row = conn.execute(
            "SELECT * FROM inventory_items WHERE id=?", (item_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Raqam topilmadi")
    row = dict(row)
    if user["role"] == "seller" and int(row["store_id"]) != require_seller_store(user):
        raise HTTPException(status_code=403, detail="Bu sizning do'koningiz emas")
    try:
        bot.delete_item_number(int(row["store_id"]), row["number"], user_id=user.get("id"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"ok": True}


@app.post("/api/branch/restock")
async def api_branch_restock(request: Request):
    user = require_panel_user(request)
    payload = await json_payload(request)
    inventory_id = payload.get("inventory_id")
    numbers = payload.get("numbers") or []
    if isinstance(numbers, str):
        numbers = [n for n in numbers.replace("\n", ",").split(",") if n.strip()]
    if not inventory_id or not numbers:
        raise HTTPException(status_code=422, detail="Mahsulot va raqamlar kerak")
    product = bot.inventory_get(int(inventory_id))
    if not product:
        raise HTTPException(status_code=404, detail="Mahsulot topilmadi")
    if user["role"] == "seller" and product["store_id"] != require_seller_store(user):
        raise HTTPException(status_code=403, detail="Bu sizning do'koningiz emas")
    try:
        updated = bot.branch_restock(int(inventory_id), numbers, user_id=user.get("id"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return (
        seller_product_dict(updated)
        if user["role"] == "seller"
        else product_dict(updated)
    )


@app.get("/api/users")
async def api_users(request: Request):
    require_admin(request)
    stores = {s["id"]: s["name"] for s in bot.list_stores()}
    result = []
    for user in bot.list_users():
        entry = dict(user)
        entry["store_name"] = stores.get(user.get("store_id"))
        entry["outstanding_debt"] = (
            bot.seller_outstanding_debt(user["id"]) if user["role"] == "seller" else 0
        )
        result.append(entry)
    return result


@app.post("/api/users")
async def api_create_user(request: Request):
    require_admin(request)
    payload = await json_payload(request)
    try:
        user = bot.create_user(
            str(payload.get("login", "")),
            str(payload.get("password", "")),
            str(payload.get("display_name", "")),
            str(payload.get("role", "seller")),
            int(payload["store_id"]) if payload.get("store_id") else None,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return user


@app.post("/api/users/{user_id}/toggle")
async def api_toggle_user(user_id: int, request: Request):
    require_admin(request)
    target = bot.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")
    if target["role"] == "admin":
        raise HTTPException(status_code=403, detail="Adminni bloklab bo'lmaydi")
    bot.set_user_active(user_id, not target["is_active"])
    return bot.get_user(user_id)


@app.delete("/api/users/{user_id}")
async def api_delete_user(user_id: int, request: Request):
    require_admin(request)
    target = bot.get_user(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")
    if target["role"] == "admin":
        raise HTTPException(status_code=403, detail="Adminni o'chirib bo'lmaydi")
    try:
        deleted = bot.delete_user(user_id)
    except ValueError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    if not deleted:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")
    return {"ok": True}


@app.get("/api/settlements")
async def api_settlements(request: Request, store_id: int | None = None, day: str | None = None):
    require_admin(request)
    if store_id is None:
        branch = bot.branch_store()
        store_id = branch["id"] if branch else bot.main_store_id()
    selected_day = day or datetime.now(SHOP_TIMEZONE).date().isoformat()
    rows = []
    conn = bot.db()
    try:
        settlements = {
            row["seller_id"]: dict(row)
            for row in conn.execute(
                "SELECT * FROM cash_settlements WHERE store_id=? AND date=?",
                (store_id, selected_day),
            ).fetchall()
        }
    finally:
        conn.close()
    for seller in bot.list_users():
        if seller["role"] != "seller" or not seller["is_active"]:
            continue
        if seller.get("store_id") not in (store_id, None):
            continue
        sales = bot.store_day_sales(store_id, selected_day, seller["id"])
        rows.append(
            {
                "seller": seller,
                "sales_total": sales["revenue"],
                "sales_count": sales["sales_count"],
                "unpriced_count": sales["unpriced_count"],
                "settlement": settlements.get(seller["id"]),
                "outstanding_debt": bot.seller_outstanding_debt(seller["id"]),
            }
        )
    return {"date": selected_day, "store_id": store_id, "rows": rows}


@app.post("/api/settlements")
async def api_create_settlement(request: Request):
    admin = require_admin(request)
    payload = await json_payload(request)
    store_id = payload.get("store_id")
    if store_id is None:
        branch = bot.branch_store()
        store_id = branch["id"] if branch else bot.main_store_id()
    day = str(payload.get("date") or datetime.now(SHOP_TIMEZONE).date().isoformat())
    try:
        result = bot.create_settlement(
            int(store_id),
            int(payload["seller_id"]) if payload.get("seller_id") else None,
            day,
            float(payload.get("received_amount") or 0),
            created_by=admin.get("app_user", {}).get("id") if admin.get("app_user") else None,
            note=str(payload.get("note", "")),
        )
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return result


@app.get("/api/debts")
async def api_debts(request: Request, store_id: int | None = None):
    require_admin(request)
    users = {u["id"]: u for u in bot.list_users()}
    result = []
    for debt in bot.seller_debts(store_id=store_id):
        entry = dict(debt)
        seller = users.get(debt.get("seller_id")) or {}
        entry["seller_name"] = seller.get("display_name") or seller.get("login")
        result.append(entry)
    return result


@app.post("/api/debts/{settlement_id}/payments")
async def api_debt_payment(settlement_id: int, request: Request):
    admin = require_admin(request)
    payload = await json_payload(request)
    try:
        bot.record_debt_payment(
            settlement_id,
            float(payload.get("amount") or 0),
            created_by=admin.get("app_user", {}).get("id") if admin.get("app_user") else None,
            note=str(payload.get("note", "")),
        )
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"ok": True}


@app.post("/webhook")
async def telegram_webhook(request: Request):
    if telegram_application is None:
        raise HTTPException(status_code=503, detail="Bot is starting")

    received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(received_secret, WEBHOOK_SECRET):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    update = Update.de_json(await request.json(), telegram_application.bot)
    await telegram_application.process_update(update)
    await telegram_application.update_persistence()
    return {"ok": True}
