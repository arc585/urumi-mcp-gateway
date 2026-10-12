"""A fake WooCommerce REST API (the subset our MCP server uses), for offline tests.

It behaves like the real one where it matters to us:
- Basic auth with the REST key pair is accepted only when the request claims HTTPS (X-Forwarded-Proto),
  exactly the quirk that made the MCP server send that header;
- permalinks come back on the internal https:// host, so the MCP server must rewrite them;
- date_created_gmt is naive UTC; errors are {"code", "message"} JSON with the matching status;
- GET /_last returns the most recent request (method, path, params, selected headers, body), so tests
  can assert what the MCP server actually sent.
"""

import asyncio
import base64
import json
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

CONSUMER_KEY, CONSUMER_SECRET = "ck_test_key", "cs_test_secret"
INTERNAL = "https://urumi-wordpress"
TZ = ZoneInfo("Asia/Kolkata")


def _gmt(day: date, hour: int) -> str:
    """Naive UTC string for <hour>:00 store-local time on <day>, as WooCommerce reports date_created_gmt."""
    return datetime.combine(day, time(hour), tzinfo=TZ).astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds")


TODAY = datetime.now(TZ).date()
STATE: dict = {
    "last": None,
    "products": [
        {"id": 7, "name": "Urumi Coffee Mug", "price": "499", "status": "publish", "stock_status": "instock",
         "permalink": f"{INTERNAL}/product/urumi-coffee-mug/", "date_created_gmt": _gmt(TODAY - timedelta(days=30), 9)},
    ],
    "orders": {
        12: {"id": 12, "number": "12", "status": "processing", "total": "998.00", "currency": "INR",
             "date_created_gmt": _gmt(TODAY, 10), "payment_method_title": "Cash on delivery", "customer_note": "Ring the bell",
             "billing": {"first_name": "Demo", "last_name": "Shopper", "email": "secret@example.test", "phone": "9999999999",
                         "address_1": "1 MG Road", "city": "Mysuru", "country": "IN"},
             "line_items": [{"product_id": 7, "name": "Urumi Coffee Mug", "quantity": 2, "total": "998.00"}]},
        11: {"id": 11, "number": "11", "status": "completed", "total": "499.00", "currency": "INR",
             "date_created_gmt": _gmt(TODAY - timedelta(days=1), 10), "payment_method_title": "Cash on delivery", "customer_note": "",
             "billing": {"first_name": "Old", "last_name": "Buyer", "email": "old@example.test", "phone": "1", "address_1": "x",
                         "city": "Pune", "country": "IN"},
             "line_items": [{"product_id": 7, "name": "Urumi Coffee Mug", "quantity": 1, "total": "499.00"}]},
    },
}


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"code": code, "message": message, "data": {"status": status}}, status_code=status)


def _auth_ok(request: Request) -> bool:
    if request.headers.get("x-forwarded-proto") != "https":
        return False  # WooCommerce ignores Basic auth on what it believes is plain HTTP
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return False
    user, _, password = base64.b64decode(header[6:]).decode().partition(":")
    return (user, password) == (CONSUMER_KEY, CONSUMER_SECRET)


def api(handler):
    async def wrapped(request: Request) -> JSONResponse:
        body = await request.body()
        STATE["last"] = {
            "method": request.method, "path": request.url.path, "params": dict(request.query_params),
            "headers": {k: v for k, v in request.headers.items() if k in ("x-forwarded-proto", "authorization", "user-agent", "accept")},
            "json": json.loads(body) if body else None,
        }
        if not _auth_ok(request):
            return _error(401, "woocommerce_rest_cannot_view", "Sorry, you cannot list resources.")
        return await handler(request)
    return wrapped


@api
async def list_products(request: Request) -> JSONResponse:
    q = request.query_params
    rows = [p for p in STATE["products"] if q.get("status", "any") in ("any", p["status"])]
    if q.get("search"):
        rows = [p for p in rows if q["search"].lower() in p["name"].lower()]
    rows.sort(key=lambda p: p["date_created_gmt"], reverse=True)
    return JSONResponse(rows[: int(q.get("per_page", 10))])


@api
async def create_product(request: Request) -> JSONResponse:
    body = STATE["last"]["json"]
    product = {"id": 100 + len(STATE["products"]), "name": body["name"], "price": body.get("regular_price"),
               "status": body.get("status", "publish"), "stock_status": "instock",
               "permalink": f"{INTERNAL}/product/{body['name'].lower().replace(' ', '-')}/",
               "date_created_gmt": datetime.now(UTC).replace(tzinfo=None).isoformat(timespec="seconds")}
    STATE["products"].append(product)
    return JSONResponse(product, status_code=201)


@api
async def list_orders(request: Request) -> JSONResponse:
    q = request.query_params
    rows = [o for o in STATE["orders"].values() if q.get("status", "any") in ("any", o["status"])]
    if q.get("after"):
        rows = [o for o in rows if o["date_created_gmt"] >= q["after"]]
    if q.get("before"):
        rows = [o for o in rows if o["date_created_gmt"] < q["before"]]
    rows.sort(key=lambda o: o["date_created_gmt"], reverse=True)
    return JSONResponse(rows[: int(q.get("per_page", 10))])


@api
async def get_order(request: Request) -> JSONResponse:
    order_id = int(request.path_params["order_id"])
    if order_id == 999:
        await asyncio.sleep(3)  # slower than the MCP server's timeout in tests
    order = STATE["orders"].get(order_id)
    if order is None:
        return _error(404, "woocommerce_rest_shop_order_invalid_id", "Invalid ID.")
    return JSONResponse(order)


@api
async def update_order(request: Request) -> JSONResponse:
    order = STATE["orders"].get(int(request.path_params["order_id"]))
    if order is None:
        return _error(404, "woocommerce_rest_shop_order_invalid_id", "Invalid ID.")
    order["status"] = STATE["last"]["json"]["status"]
    return JSONResponse(order)


async def last(request: Request) -> JSONResponse:
    return JSONResponse(STATE["last"])


async def health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


app = Starlette(routes=[
    Route("/wp-json/wc/v3/products", list_products, methods=["GET"]),
    Route("/wp-json/wc/v3/products", create_product, methods=["POST"]),
    Route("/wp-json/wc/v3/orders", list_orders, methods=["GET"]),
    Route("/wp-json/wc/v3/orders/{order_id:int}", get_order, methods=["GET"]),
    Route("/wp-json/wc/v3/orders/{order_id:int}", update_order, methods=["PUT"]),
    Route("/_last", last),
    Route("/_health", health),
])
