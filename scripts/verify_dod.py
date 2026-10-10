"""Automated run of the assignment's definition of done against the live local cluster.

Runs every check from docs/DEMO_CHECKLIST.md through the same public entry points a person uses
(storefront, gateway API, /mcp, dashboard chat API) plus kubectl for the Pod-failure steps, and prints
PASS/FAIL per step. Creates two throwaway members (dod-a-*, dod-b-*) and cleans up afterwards.

  .venv/bin/python scripts/verify_dod.py                # full run (~4 min: chat waits out the Gemini rate limit)
  .venv/bin/python scripts/verify_dod.py --skip-chat    # user A uses MCP instead of the chat (no LLM quota)
  .venv/bin/python scripts/verify_dod.py --preflight    # only check that everything is up

Secrets are read from Kubernetes Secrets at runtime and never printed.
"""

import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
import time

import httpx
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

NS = os.getenv("URUMI_NAMESPACE", "urumi")
# Local defaults; for a VPS: URUMI_APP=https://app.example.com URUMI_STORE=https://store.example.com
APP = os.getenv("URUMI_APP", "http://app.urumi.test:8880")
STORE = os.getenv("URUMI_STORE", "http://store.urumi.test:8880")
MCP = f"{APP}/mcp"
CHAT_GAP_SECONDS = 45  # free-tier Gemini allows only a few requests per minute

results: list[tuple[bool, str, str]] = []


def check(ok: bool, step: str, detail: str = "") -> bool:
    results.append((ok, step, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {step}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


def kubectl(*args: str) -> str:
    return subprocess.run(["kubectl", "-n", NS, *args], capture_output=True, text=True, check=True).stdout


def pods(component: str) -> list[dict]:
    """Live (not terminating) Pods of a component, with their readiness."""
    items = json.loads(kubectl("get", "pods", "-l", f"app.kubernetes.io/component={component}", "-o", "json"))["items"]
    return [{"name": p["metadata"]["name"],
             "ready": all(c.get("ready") for c in p["status"].get("containerStatuses", [])) and bool(p["status"].get("containerStatuses"))}
            for p in items if not p["metadata"].get("deletionTimestamp")]


def wait_until(condition, seconds: int = 120) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(2)
    return False


GATEWAY_SECRET = os.getenv("URUMI_GATEWAY_SECRET", "urumi-gateway")   # prod: urumi-gateway-credentials
STORE_SECRET = os.getenv("URUMI_STORE_SECRET", "urumi-store")         # prod: urumi-store-credentials


def secret(name: str, key: str) -> str:
    return base64.b64decode(kubectl("get", "secret", name, "-o", f"jsonpath={{.data.{key}}}")).decode()


# --------------------------------------------------------------------------- clients


class Api:
    def __init__(self, key: str) -> None:
        self.http = httpx.Client(base_url=f"{APP}/api/v1", headers={"Authorization": f"Bearer {key}"}, timeout=180)

    def get(self, path: str, **params):
        return self.http.get(path, params=params)

    def post(self, path: str, body=None):
        return self.http.post(path, json=body)

    def patch(self, path: str, body):
        return self.http.patch(path, json=body)


async def _mcp(key: str, tool: str | None, arguments: dict | None):
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {key}"}, timeout=60) as http:
        async with Client(streamable_http_client(MCP, http_client=http)) as client:
            if tool is None:
                return [t.name for t in (await client.list_tools()).tools]
            return await client.call_tool(tool, arguments or {})


def mcp_tools(key: str) -> list[str]:
    return asyncio.run(_mcp(key, None, None))


def mcp_call(key: str, tool: str, arguments: dict):
    return asyncio.run(_mcp(key, tool, arguments))


def raw_mcp_status(key: str) -> int:
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if key:  # no key at all = no Authorization header
        headers["Authorization"] = f"Bearer {key}"
    response = httpx.post(MCP, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=headers, timeout=30)
    return response.status_code


def chat(api: Api, message: str) -> dict:
    """One chat message; waits and retries if the provider rate-limits."""
    for attempt in range(3):
        response = api.post("/chat", {"messages": [{"role": "user", "content": message}]})
        if response.status_code == 200:
            return response.json()
        detail = response.json().get("detail", "")
        if "429" in detail and attempt < 2:
            print(f"      (model rate-limited; waiting 40 s and retrying)", flush=True)
            time.sleep(40)
            continue
        raise RuntimeError(f"chat failed: HTTP {response.status_code}: {detail}")
    raise RuntimeError("chat failed after retries")


def store_checkout(product_id: int, quantity: int, city: str) -> int:
    """Buy a product through the storefront's own Store API (what the checkout page uses), paying COD."""
    with httpx.Client(base_url=f"{STORE}/wp-json/wc/store/v1", timeout=60) as shop:
        nonce = shop.get("/cart").headers["nonce"]
        headers = {"Nonce": nonce}
        shop.post("/cart/add-item", json={"id": product_id, "quantity": quantity}, headers=headers).raise_for_status()
        address = {"first_name": "Demo", "last_name": "Shopper", "address_1": "1 MG Road", "city": city,
                   "state": "KA", "postcode": "560001", "country": "IN"}
        order = shop.post("/checkout", headers=headers, json={
            "billing_address": {**address, "email": "demo-shopper@example.test", "phone": "9999999999"},
            "shipping_address": address, "payment_method": "cod"}).json()
    if "order_id" not in order:
        raise RuntimeError(f"checkout failed: {order.get('message', order)}")
    return order["order_id"]


def storefront_product(name: str) -> dict | None:
    products = httpx.get(f"{STORE}/wp-json/wc/store/v1/products", params={"search": name}, timeout=30).json()
    return next((p for p in products if p["name"] == name), None)


def statuses(api: Api) -> dict[str, dict]:
    return {row["id"]: row for row in api.get("/upstreams/status").json()}


def structured(result) -> dict:
    return result.structured_content or {}


# --------------------------------------------------------------------------- steps


def preflight(admin: Api) -> bool:
    pods = json.loads(kubectl("get", "pods", "-o", "json"))["items"]
    not_ready = [p["metadata"]["name"] for p in pods if p["status"]["phase"] != "Succeeded"
                 and not all(c.get("ready") for c in p["status"].get("containerStatuses", []))]
    ok = check(not not_ready, "preflight: all Pods ready", ", ".join(not_ready) or f"{len(pods)} Pods")
    ok &= check(httpx.get(f"{STORE}/shop/", timeout=30).status_code == 200, "preflight: storefront reachable via Ingress")
    ok &= check(httpx.get(f"{APP}/", timeout=30).status_code == 200, "preflight: dashboard reachable via Ingress")
    ok &= check(raw_mcp_status("") == 401, "preflight: /mcp rejects requests without a key")
    down = [k for k, v in statuses(admin).items() if v["status"] != "Connected"]
    ok &= check(not down, "preflight: all upstreams Connected", ", ".join(down) or "woocommerce, store-info, shipping-info")
    return ok


def run(skip_chat: bool, keep: bool) -> None:
    admin_key = secret(GATEWAY_SECRET, "bootstrap-admin-key")
    admin = Api(admin_key)
    if not preflight(admin):
        return
    stamp = time.strftime("%H%M%S")
    created_orders: list[int] = []
    users: dict[str, dict] = {}
    product_name = f"DoD Demo Mug {stamp}"
    product_id: int | None = None
    try:
        # 1. Store: order through the storefront, visible to the store admin side.
        mug = storefront_product("Urumi Coffee Mug")
        order_id = store_checkout(mug["id"], 1, "Bengaluru")
        created_orders.append(order_id)
        order = structured(mcp_call(admin_key, "woocommerce__get_order", {"order_id": order_id}))
        check(order.get("status") == "processing", "store: storefront COD checkout creates an order",
              f"order #{order_id}, status {order.get('status')} (as listed in WooCommerce → Orders)")

        # 2. Admin invites two members.
        for who in ("a", "b"):
            response = admin.post("/users", {"display_name": f"DoD User {who.upper()}", "email": f"dod-{who}-{stamp}@example.test"})
            users[who] = response.json()
            check(response.status_code == 201 and users[who]["api_key"].startswith("urm_"),
                  f"admin: invite user {who.upper()}", f"{users[who].get('email')} (key shown once)")
        user_a, user_b = Api(users["a"]["api_key"]), Api(users["b"]["api_key"])

        # 3. User A: create a product via the assistant, buy it, ask for today's orders.
        if not skip_chat:
            try:
                reply = chat(user_a, f'Create a product called "{product_name}" priced at 299, published.')
                created = [c for c in reply["tool_calls"] if c["tool"] == "create_product" and c["ok"]]
                check(bool(created), "user A: assistant creates a product",
                      f"model {reply['model']}, tools {[c['name'] for c in reply['tool_calls']]}")
            except RuntimeError as exc:  # LLM unavailable: record it, continue the rest via MCP
                check(False, "user A: assistant creates a product", str(exc)[:200])
                skip_chat = True
        if skip_chat and not storefront_product(product_name):
            result = mcp_call(users["a"]["api_key"], "woocommerce__create_product", {"name": product_name, "price": "299"})
            check(not result.is_error, "user A: create product (via MCP instead of the assistant)", product_name)
        product = storefront_product(product_name)
        product_id = product["id"] if product else None
        check(bool(product) and product["prices"]["price"] == "29900", "user A: product appears on the storefront",
              product["permalink"] if product else "not found")
        a_order = store_checkout(product_id, 1, "Mysuru") if product_id else None
        if a_order:
            created_orders.append(a_order)
        check(bool(a_order), "user A: buys it on the storefront (COD)", f"order #{a_order}")
        if skip_chat:
            listed = structured(mcp_call(users["a"]["api_key"], "woocommerce__list_orders", {"on_date": "today", "limit": 20}))
            ids = [o["id"] for o in listed.get("orders", [])]
            check(a_order in ids, "user A: today's orders include that order (via MCP)", f"ids {ids}")
        else:
            print(f"      (waiting {CHAT_GAP_SECONDS} s between assistant messages for the free-tier rate limit)", flush=True)
            time.sleep(CHAT_GAP_SECONDS)
            try:
                reply = chat(user_a, "Show today's orders with their order IDs and items.")
                check(str(a_order) in reply["reply"] and any(c["tool"] == "list_orders" for c in reply["tool_calls"]),
                      "user A: assistant reports today's orders including it", reply["reply"].replace("\n", " ")[:160])
            except RuntimeError as exc:
                check(False, "user A: assistant reports today's orders", str(exc)[:200])

        # 4. User B: external MCP client with only the URL and B's key.
        names = mcp_tools(users["b"]["api_key"])
        prefixes = sorted({n.split("__")[0] for n in names})
        check(len(names) >= 8 and prefixes == ["shipping-info", "store-info", "woocommerce"],
              "user B: MCP client sees all enabled servers' tools", f"{len(names)} tools from {prefixes}")
        listed = structured(mcp_call(users["b"]["api_key"], "woocommerce__list_orders", {"on_date": "today", "limit": 20}))
        check(a_order in [o["id"] for o in listed.get("orders", [])], "user B: queries today's orders", f"{listed.get('count')} orders today")
        detail = structured(mcp_call(users["b"]["api_key"], "woocommerce__get_order", {"order_id": a_order}))
        estimate = structured(mcp_call(users["b"]["api_key"], "shipping-info__estimate_shipping",
                                       {"destination_city": detail.get("customer_city") or "Mysuru",
                                        "order_total": float(detail.get("total") or 0)}))
        check("estimated_business_days" in estimate, "user B: one task across two upstreams (order → shipping estimate)",
              f"{estimate.get('estimated_business_days')} days, ₹{estimate.get('shipping_cost_inr')}")

        # 5. Revoke B's keys: the very next call is rejected.
        before = raw_mcp_status(users["b"]["api_key"])
        admin.post(f"/users/{users['b']['id']}/revoke-keys")
        after = raw_mcp_status(users["b"]["api_key"])
        check(before == 200 and after == 401, "admin: revoke user B → next call rejected", f"HTTP {before} → {after}")

        # 6. Disable an upstream: its tools vanish for everyone; re-enable restores them.
        admin.patch("/upstreams/shipping-info", {"enabled": False})
        names = mcp_tools(users["a"]["api_key"])
        refused = mcp_call(users["a"]["api_key"], "shipping-info__estimate_shipping", {"destination_city": "Pune", "order_total": 10})
        check(not [n for n in names if n.startswith("shipping-info__")] and refused.is_error,
              "admin: disable shipping-info → tools gone, calls refused", f"{len(names)} tools left")
        check(statuses(admin)["shipping-info"]["status"] == "Disabled", "dashboard: shipping-info shown as Disabled")
        admin.patch("/upstreams/shipping-info", {"enabled": True})
        check(any(n.startswith("shipping-info__") for n in mcp_tools(users["a"]["api_key"])), "admin: re-enable → tools back")

        # 7. Pod failure. (a) delete a Pod: the Deployment replaces it. (b) scale to 0: sustained outage.
        old = pods("store-info-mcp")[0]["name"]
        kubectl("delete", "pod", old, "--wait=false")
        replaced = wait_until(lambda: any(p["name"] != old and p["ready"] for p in pods("store-info-mcp")))
        new = next((p["name"] for p in pods("store-info-mcp") if p["name"] != old), "none")
        check(replaced, "kubernetes: deleted upstream Pod is replaced by its Deployment", f"{old} → {new}")

        kubectl("scale", "deploy/urumi-shipping-info-mcp", "--replicas=0")
        wait_until(lambda: not pods("shipping-info-mcp") and not kubectl(
            "get", "pods", "-l", "app.kubernetes.io/component=shipping-info-mcp", "-o", "name").strip())
        status = statuses(admin)["shipping-info"]
        check(status["status"] == "Unreachable", "dashboard: stopped upstream shown Unreachable", status.get("error") or "")
        started = time.perf_counter()
        healthy = mcp_call(users["a"]["api_key"], "woocommerce__list_products", {"limit": 1})
        store_ok = mcp_call(users["a"]["api_key"], "store-info__lookup_store_info", {"topic": "hours"})
        seconds = time.perf_counter() - started
        check(not healthy.is_error and not store_ok.is_error, "other upstreams keep working while one is down", f"2 calls in {seconds:.1f} s")
        dead = mcp_call(users["a"]["api_key"], "shipping-info__estimate_shipping", {"destination_city": "Pune", "order_total": 10})
        check(dead.is_error, "call to the stopped upstream fails with a clear error", dead.content[0].text if dead.content else "")
        kubectl("scale", "deploy/urumi-shipping-info-mcp", "--replicas=1")
        kubectl("rollout", "status", "deploy/urumi-shipping-info-mcp", "--timeout=120s")
        check(statuses(admin)["shipping-info"]["status"] == "Connected", "upstream back to Connected after scaling up")

        # 8. Audit log: everything above, attributed correctly.
        rows_a = admin.get("/audit", user_id=users["a"]["id"], limit=50).json()
        rows_b = admin.get("/audit", user_id=users["b"]["id"], limit=50).json()
        channel = "mcp" if skip_chat else "chat"  # skip_chat also becomes True if the assistant was unavailable
        check(any(r["tool_name"] == "create_product" and r["channel"] == channel for r in rows_a)
              and all(r["user_email"] == users["a"]["email"] for r in rows_a),
              "audit: user A's calls attributed to A", f"{len(rows_a)} calls, incl. create_product via {channel}")
        check({r["tool_name"] for r in rows_b} >= {"list_orders", "get_order", "estimate_shipping"}
              and all(r["user_email"] == users["b"]["email"] and r["channel"] == "mcp" for r in rows_b),
              "audit: user B's calls attributed to B (MCP client)", f"{len(rows_b)} calls")
        check(any(not r["succeeded"] for r in rows_a), "audit: failed calls recorded with their error",
              next((r["error"] for r in rows_a if not r["succeeded"]), ""))
        events = admin.get("/admin-events", limit=100).json()
        actions = {(e["action"], e["target"]) for e in events}
        wanted = {("user.invited", users["a"]["email"]), ("user.invited", users["b"]["email"]),
                  ("key.revoked_all", users["b"]["email"]), ("upstream.disabled", "shipping-info"),
                  ("upstream.enabled", "shipping-info")}
        check(wanted <= actions, "audit: admin actions recorded (invite, revoke, disable, enable)",
              f"{len(wanted & actions)}/{len(wanted)} found")
    except Exception as exc:  # report and still clean up
        check(False, "unexpected error", f"{type(exc).__name__}: {exc}")
    finally:
        if not keep:
            cleanup(admin_key, users, created_orders, product_id)


def cleanup(admin_key: str, users: dict, orders: list[int], product_id: int | None) -> None:
    print("--- cleanup", flush=True)
    admin = Api(admin_key)
    kubectl("scale", "deploy/urumi-shipping-info-mcp", "--replicas=1")
    admin.patch("/upstreams/shipping-info", {"enabled": True})
    for order_id in orders:  # also exercises the fifth WooCommerce tool
        result = mcp_call(admin_key, "woocommerce__update_order_status", {"order_id": order_id, "status": "cancelled"})
        print(f"      order #{order_id} → cancelled" if not result.is_error else f"      order #{order_id}: {result.content[0].text}")
    for user in users.values():
        if user.get("id"):
            admin.post(f"/users/{user['id']}/revoke-keys")
            admin.patch(f"/users/{user['id']}", {"active": False})
            print(f"      deactivated {user['email']}")
    if product_id:
        forward = subprocess.Popen(["kubectl", "-n", NS, "port-forward", "svc/urumi-wordpress", "18081:80"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            time.sleep(2)
            auth = (secret(STORE_SECRET, "woocommerce-consumer-key"), secret(STORE_SECRET, "woocommerce-consumer-secret"))
            httpx.delete(f"http://127.0.0.1:18081/wp-json/wc/v3/products/{product_id}", auth=auth,
                         headers={"X-Forwarded-Proto": "https"}, timeout=30)
            print(f"      product #{product_id} moved to trash")
        finally:
            forward.terminate()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--skip-chat", action="store_true", help="user A uses MCP instead of the LLM assistant")
    parser.add_argument("--keep", action="store_true", help="do not clean up test users, orders, and product")
    parser.add_argument("--preflight", action="store_true", help="only run the preflight checks")
    args = parser.parse_args()
    if args.preflight:
        preflight(Api(secret(GATEWAY_SECRET, "bootstrap-admin-key")))
    else:
        run(args.skip_chat, args.keep)
    failed = [r for r in results if not r[0]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
