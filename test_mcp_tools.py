"""
test_mcp_tools.py
-----------------
Full integration test for every RetailOps MCP tool group.

Exercises all tools via FastMCP.call_tool() — the same code path a real
MCP client uses. Requires the Django dev server to be running:

    python manage.py runserver

Run with:
    python test_mcp_tools.py

Exit code 0 = all tests passed.
Exit code 1 = one or more tests failed.

Test groups
-----------
  Core groups (original smoke test):
    dashboard, auth, customers, categories, products, inventory, orders,
    payments, users, resources, prompts

  Extended coverage (gap-closing):
    settings      — retailops_get/update_system_settings, no-op guard
    bulk_ops      — bulk_adjust_inventory, bulk_confirm/ship/deliver_orders,
                    partial-success response shape, empty-list guards
    full_lifecycle — complete order journey: Draft→Pending→Confirmed→Paid
                    (auto-transition on full payment)→Shipped→Delivered
    pagination    — page_size=1, page=2, page_size clamped to 100
"""

import asyncio
import base64
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Unique suffix for all test data created in this run — prevents conflicts when
# previous runs did not clean up successfully (e.g. due to a mid-run failure).
_TS = str(int(time.time()))[-6:]   # last 6 digits of unix timestamp

# Force UTF-8 output so Unicode characters in labels/messages don't crash on
# Windows terminals that default to cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── Import the wired server (registers all tools, resources, prompts) ──────
from mcp_server.server import mcp


# ── Result tracking ────────────────────────────────────────────────────────

_results: list[tuple[str, str, str]] = []   # (group, name, status/detail)
_created: dict[str, Any] = {}               # IDs of records created during the run


def _record(group: str, name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    _results.append((group, name, status, detail))
    marker = "[PASS]" if ok else "[FAIL]"
    print(f"  {marker}  {name:<55}  {detail}")


async def _call(tool: str, args: dict = None) -> Any:
    """Call a tool and return its result, propagating any exception."""
    return await mcp.call_tool(tool, args or {})


async def _call_ok(group: str, label: str, tool: str, args: dict = None) -> Any:
    """Call a tool, record PASS on success or FAIL on exception. Return result or None."""
    try:
        result = await _call(tool, args or {})
        _record(group, label, True)
        return result
    except Exception as e:
        _record(group, label, False, str(e)[:120])
        return None


async def _call_expect_error(group: str, label: str, tool: str, args: dict = None) -> bool:
    """Call a tool and expect it to raise an exception (error-path test)."""
    try:
        await _call(tool, args or {})
        _record(group, label, False, "Expected error but call succeeded")
        return False
    except Exception:
        _record(group, label, True, "(error correctly raised)")
        return True


# ── Helpers ────────────────────────────────────────────────────────────────

def _first_result(response: Any) -> dict | None:
    """Extract first dict from a FastMCP call_tool response."""
    if isinstance(response, dict):
        return response
    if isinstance(response, list) and response:
        item = response[0]
        if hasattr(item, "text"):
            import json
            try:
                return json.loads(item.text)
            except Exception:
                return None
        if isinstance(item, dict):
            return item
    return None


def _get(data: Any, *keys: str) -> Any:
    """Safe nested key lookup on a result."""
    parsed = _first_result(data)
    if parsed is None:
        return None
    for key in keys:
        if isinstance(parsed, dict):
            parsed = parsed.get(key)
        else:
            return None
    return parsed


# ══════════════════════════════════════════════════════════════════════════════
# Test groups
# ══════════════════════════════════════════════════════════════════════════════

async def test_dashboard():
    print("\n--- Dashboard ---")
    r = await _call_ok("dashboard", "retailops_get_dashboard", "retailops_get_dashboard")
    ok = _get(r, "orders_this_month") is not None
    _record("dashboard", "dashboard has orders_this_month key", ok)


async def test_auth():
    print("\n--- Auth ---")
    # login with correct credentials
    r = await _call_ok(
        "auth", "retailops_login (valid credentials)",
        "retailops_login",
        {"email": "mcp-agent@retailops.local", "password": "MCPAgentPass123!"},
    )
    _record("auth", "login returns token", _get(r, "token") is not None)

    # login with wrong password — must fail
    await _call_expect_error(
        "auth", "retailops_login (bad credentials ->error)",
        "retailops_login",
        {"email": "nobody@nowhere.com", "password": "wrong"},
    )
    # Note: we do NOT call retailops_logout here — that would revoke the
    # agent token and break all subsequent tests.


async def test_customers():
    print("\n--- Customers ---")

    r = await _call_ok("customers", "retailops_list_customers",
                       "retailops_list_customers")
    initial_count = _get(r, "count") or 0

    # search
    await _call_ok("customers", "retailops_list_customers (search)",
                   "retailops_list_customers", {"search": "a", "page_size": 5})

    # create — address_line1, city, state, postal_code are required by the serializer
    r = await _call_ok("customers", "retailops_create_customer",
                       "retailops_create_customer", {
                           "first_name": "Smoke",
                           "last_name": "Test",
                           "email": f"smoke.{_TS}@example.com",
                           "address_line1": "123 Test Street",
                           "city": "Test City",
                           "state": "CA",
                           "postal_code": "90001",
                           "country": "United States",
                       })
    cid = _get(r, "id")
    _record("customers", "create returns id", cid is not None)
    _created["customer_id"] = cid

    if cid:
        # get
        await _call_ok("customers", "retailops_get_customer",
                       "retailops_get_customer", {"id": cid})

        # update
        r = await _call_ok("customers", "retailops_update_customer",
                           "retailops_update_customer",
                           {"id": cid, "city": "Updated City"})
        _record("customers", "update reflects new city",
                _get(r, "city") == "Updated City")

    # 404 path
    await _call_expect_error("customers", "retailops_get_customer (missing id ->404)",
                             "retailops_get_customer", {"id": 999999})


async def test_categories():
    print("\n--- Categories ---")

    await _call_ok("categories", "retailops_list_categories",
                   "retailops_list_categories")

    r = await _call_ok("categories", "retailops_create_category",
                       "retailops_create_category",
                       {"name": f"MCP-Smoke-{_TS}"})
    cat_id = _get(r, "id")
    _record("categories", "create returns id", cat_id is not None)
    _created["category_id"] = cat_id

    if cat_id:
        await _call_ok("categories", "retailops_get_category",
                       "retailops_get_category", {"id": cat_id})

        r = await _call_ok("categories", "retailops_update_category",
                           "retailops_update_category",
                           {"id": cat_id, "description": "Created by smoke test"})
        _record("categories", "update reflects description",
                _get(r, "description") == "Created by smoke test")


async def test_products():
    print("\n--- Products ---")

    await _call_ok("products", "retailops_list_products",
                   "retailops_list_products")
    await _call_ok("products", "retailops_list_products (stock=low)",
                   "retailops_list_products", {"stock": "low"})
    await _call_ok("products", "retailops_list_products (stock=out)",
                   "retailops_list_products", {"stock": "out"})

    # Use an existing category (or the one we just created)
    cat_id = _created.get("category_id")
    if not cat_id:
        # Fall back to first seeded category
        r = await _call("retailops_list_categories", {"page_size": 1})
        items = _get(r, "results")
        cat_id = items[0]["id"] if items else 1

    r = await _call_ok("products", "retailops_create_product",
                       "retailops_create_product", {
                           "sku": f"SMOKE-{_TS}",
                           "name": f"Smoke Test Product {_TS}",
                           "category_id": cat_id,
                           "unit_price": "9.99",
                           "unit_of_measure": "piece",
                           "low_stock_threshold": 5,
                           "external_image_url": "https://example.com/retailops-smoke-product.png",
                       })
    pid = _get(r, "id")
    _record("products", "create returns id", pid is not None)
    _created["product_id"] = pid

    if pid:
        r = await _call_ok("products", "retailops_get_product",
                           "retailops_get_product", {"id": pid})
        _record("products", "get returns current_stock=0 (new product)",
                _get(r, "current_stock") == 0)

        r = await _call_ok("products", "retailops_update_product",
                           "retailops_update_product",
                           {"id": pid, "unit_price": "12.50"})
        _record("products", "update reflects new price",
                _get(r, "unit_price") == "12.50")
        _record("products", "created product has image metadata",
                bool(_get(r, "primary_image_url")) and _get(r, "has_image") is True)

        tiny_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/lqW2NwAAAABJRU5ErkJggg=="
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "mcp-product-smoke.png"
            image_path.write_bytes(tiny_png)
            r = await _call_ok("products", "retailops_update_product (image_path upload)",
                               "retailops_update_product",
                               {"id": pid, "image_path": str(image_path)})
            _record("products", "image_path upload preserves has_image",
                    _get(r, "has_image") is True)

        await _call_ok("products", "retailops_get_product_movements",
                       "retailops_get_product_movements", {"id": pid})


async def test_inventory():
    print("\n--- Inventory ---")

    await _call_ok("inventory", "retailops_list_inventory_movements",
                   "retailops_list_inventory_movements")

    pid = _created.get("product_id")
    if pid:
        r = await _call_ok("inventory", "retailops_adjust_inventory (+20 units)",
                           "retailops_adjust_inventory",
                           {"product_id": pid, "quantity": 20,
                            "notes": "Smoke test stock receipt"})
        inv_id = _get(r, "id")
        _record("inventory", "adjust returns movement id", inv_id is not None)

        if inv_id:
            await _call_ok("inventory", "retailops_get_inventory_movement",
                           "retailops_get_inventory_movement", {"id": inv_id})

        # Verify stock updated
        r2 = await _call("retailops_get_product", {"id": pid})
        _record("inventory", "product current_stock updated to 20",
                _get(r2, "current_stock") == 20)

    # zero-quantity guard (MCP-layer validation, not API)
    await _call_expect_error(
        "inventory", "retailops_adjust_inventory (quantity=0 ->error)",
        "retailops_adjust_inventory", {"product_id": 1, "quantity": 0},
    )


async def test_orders():
    print("\n--- Orders ---")

    await _call_ok("orders", "retailops_list_orders",
                   "retailops_list_orders")
    await _call_ok("orders", "retailops_list_orders (status=draft)",
                   "retailops_list_orders", {"status": "draft"})

    cid = _created.get("customer_id")
    pid = _created.get("product_id")

    if not cid or not pid:
        _record("orders", "SKIP create_order (no smoke customer/product)", False,
                "previous test failed to create customer or product")
        return

    # Create order
    r = await _call_ok("orders", "retailops_create_order",
                       "retailops_create_order", {
                           "customer_id": cid,
                           "items": [
                               {"product_id": pid, "quantity": 2, "unit_price": "12.50"}
                           ],
                           "notes": "Smoke test order",
                       })
    oid = _get(r, "id")
    _record("orders", "create returns id", oid is not None)
    _created["order_id"] = oid

    if not oid:
        return

    # Get
    r = await _call_ok("orders", "retailops_get_order",
                       "retailops_get_order", {"id": oid})
    _record("orders", "order status is draft",
            _get(r, "status") == "draft")

    # Update (still draft)
    r = await _call_ok("orders", "retailops_update_order",
                       "retailops_update_order",
                       {"id": oid, "notes": "Smoke test order (updated)"})
    _record("orders", "update reflects new notes",
            _get(r, "notes") == "Smoke test order (updated)")

    # Submit: draft ->pending
    r = await _call_ok("orders", "retailops_submit_order",
                       "retailops_submit_order", {"id": oid})
    _record("orders", "submit ->status is pending",
            _get(r, "status") == "pending")

    # Confirm: pending ->confirmed  (deducts stock)
    r = await _call_ok("orders", "retailops_confirm_order",
                       "retailops_confirm_order", {"id": oid})
    _record("orders", "confirm ->status is confirmed",
            _get(r, "status") == "confirmed")

    # Verify stock deducted
    r2 = await _call("retailops_get_product", {"id": pid})
    _record("orders", "product stock deducted by 2 (20-2=18)",
            _get(r2, "current_stock") == 18)

    # Cancel: confirmed ->cancelled  (restores stock)
    r = await _call_ok("orders", "retailops_cancel_order",
                       "retailops_cancel_order", {"id": oid})
    _record("orders", "cancel ->status is cancelled",
            _get(r, "status") == "cancelled")

    # Verify stock restored
    r2 = await _call("retailops_get_product", {"id": pid})
    _record("orders", "product stock restored after cancel (back to 20)",
            _get(r2, "current_stock") == 20)

    # empty items guard
    await _call_expect_error(
        "orders", "retailops_create_order (empty items ->error)",
        "retailops_create_order",
        {"customer_id": cid, "items": []},
    )


async def test_payments():
    print("\n--- Payments ---")

    await _call_ok("payments", "retailops_list_payments",
                   "retailops_list_payments")

    # Find a confirmed order from seeded data to record a payment against
    r = await _call("retailops_list_orders",
                    {"status": "confirmed", "page_size": 1})
    results = _get(r, "results")
    seeded_order = results[0] if results else None

    if seeded_order:
        seeded_oid = seeded_order["id"]

        r = await _call_ok("payments", "retailops_record_payment (mobile manual override)",
                           "retailops_record_payment", {
                               "sales_order_id": seeded_oid,
                               "amount": "0.01",
                               "payment_method": "mobile_payment",
                               "reference_number": f"MCP-MOB-{_TS}",
                               "notes": "Smoke test mobile payment manual override",
                               "origin_bank": "BDV",
                           })
        _record("payments", "mobile_payment manual override returns id", _get(r, "id") is not None)

        r = await _call_ok("payments", "retailops_record_payment",
                           "retailops_record_payment", {
                               "sales_order_id": seeded_oid,
                               "amount": "1.00",
                               "payment_method": "cash",
                               "notes": "Smoke test payment",
                           })
        pay_id = _get(r, "id")
        _record("payments", "record_payment returns id", pay_id is not None)

        if pay_id:
            await _call_ok("payments", "retailops_get_payment",
                           "retailops_get_payment", {"id": pay_id})

    else:
        _record("payments", "SKIP record_payment (no confirmed order in seed)", False,
                "No confirmed order found — payment test skipped")

    # payment on non-existent order ->error
    await _call_expect_error(
        "payments", "retailops_record_payment (bad order ->error)",
        "retailops_record_payment",
        {"sales_order_id": 999999, "amount": "1.00", "payment_method": "cash"},
    )


async def test_users():
    print("\n--- Users ---")
    # Agent token is Manager role — user tools require Admin.
    # Expect permission errors on all write ops; read ops may succeed if the
    # agent happens to have Admin, but we design around Manager.
    await _call_expect_error(
        "users", "retailops_list_users (Manager token ->403)",
        "retailops_list_users",
    )
    await _call_expect_error(
        "users", "retailops_create_user (Manager token ->403)",
        "retailops_create_user",
        {"email": "x@x.com", "password": "Password1!", "role": 3},
    )


async def test_resources():
    print("\n--- Resources (spot-check) ---")
    # Resources return JSON strings; we just verify they return without error.
    from mcp_server.resources.handlers import register_resources as _r  # already registered

    # Check via client directly (resources are harder to invoke programmatically
    # without an MCP session; we validate the underlying client calls succeed)
    from mcp_server.client import RetailOpsClient
    c = RetailOpsClient()
    try:
        import json
        for path, label in [
            ("/dashboard/",   "retailops://dashboard"),
            ("/settings/",    "retailops://settings"),
            ("/customers/",   "retailops://customers"),
            ("/products/",    "retailops://products"),
            ("/categories/",  "retailops://categories"),
            ("/orders/",      "retailops://orders"),
            ("/payments/",    "retailops://payments"),
            ("/inventory/",   "retailops://inventory"),
        ]:
            try:
                data = await c.get(path)
                _record("resources", label, True, f"count={data.get('count', '?')}")
            except Exception as e:
                _record("resources", label, False, str(e)[:80])
    finally:
        await c.close()


async def test_prompts():
    print("\n--- Prompts ---")
    prompts = list(mcp._prompt_manager.list_prompts())
    expected = {
        "retailops_create_order_workflow",
        "retailops_process_payment_workflow",
        "retailops_cancel_or_refund_workflow",
        "retailops_stock_check_workflow",
        "retailops_onboard_customer_workflow",
    }
    registered = {p.name for p in prompts}
    for name in sorted(expected):
        _record("prompts", f"prompt registered: {name}", name in registered)


# ── Extended coverage ─────────────────────────────────────────────────────────

async def test_settings():
    """
    Covers retailops_get_system_settings and retailops_update_system_settings.
    Restores original values at the end to avoid polluting other test data.
    """
    print("\n--- Settings ---")

    r = await _call_ok("settings", "retailops_get_system_settings",
                       "retailops_get_system_settings")
    _record("settings", "get returns currency_code",    _get(r, "currency_code") is not None)
    _record("settings", "get returns currency_symbol",  _get(r, "currency_symbol") is not None)
    _record("settings", "get returns decimal_places",   _get(r, "decimal_places") is not None)
    _record("settings", "get returns ocr_enabled",      _get(r, "ocr_enabled") is not None)
    _record("settings", "get returns receipt image requirement",
            _get(r, "receipt_image_required_for_receipt_methods") is not None)

    # Capture originals so we can restore them
    orig_code   = _get(r, "currency_code")   or "USD"
    orig_symbol = _get(r, "currency_symbol") or "$"
    orig_places = _get(r, "decimal_places")
    if orig_places is None:
        orig_places = 2

    # Partial update — change only decimal_places
    r2 = await _call_ok("settings", "retailops_update_system_settings (change decimal_places=3)",
                        "retailops_update_system_settings",
                        {"decimal_places": 3})
    _record("settings", "update returns decimal_places=3", _get(r2, "decimal_places") == 3)

    # Round-trip: verify persistence
    r3 = await _call_ok("settings", "retailops_get_system_settings (verify round-trip)",
                        "retailops_get_system_settings")
    _record("settings", "update persisted (decimal_places=3)", _get(r3, "decimal_places") == 3)

    # No-op guard — calling with zero fields must raise ValueError
    await _call_expect_error(
        "settings", "retailops_update_system_settings (no args →error)",
        "retailops_update_system_settings",
    )
    await _call_expect_error(
        "settings", "retailops_update_system_settings (bad OCR method ->error)",
        "retailops_update_system_settings",
        {"ocr_enabled_methods": ["wire_transfer"]},
    )

    # Restore originals so downstream tests see unmodified settings
    await _call_ok("settings", "retailops_update_system_settings (restore originals)",
                   "retailops_update_system_settings",
                   {"currency_code": orig_code, "currency_symbol": orig_symbol,
                    "decimal_places": orig_places})


async def test_bulk_operations():
    """
    Covers all four bulk tools and verifies the partial-success response shape.
    Creates two dedicated test orders (bulk_order_ids) to drive the full
    confirm→pay→ship→deliver chain without touching the main smoke order.
    """
    print("\n--- Bulk Operations ---")

    cid = _created.get("customer_id")
    pid = _created.get("product_id")
    if not cid or not pid:
        _record("bulk_ops", "SKIP (no smoke customer/product from earlier tests)", False,
                "customer or product creation failed earlier")
        return

    # ── bulk_adjust_inventory ──────────────────────────────────────────────

    r = await _call_ok("bulk_ops", "retailops_bulk_adjust_inventory (single item)",
                       "retailops_bulk_adjust_inventory",
                       {"adjustments": [
                           {"product_id": pid, "quantity": 10, "notes": "Bulk test restock"},
                       ]})
    _record("bulk_ops", "bulk_adjust response has 'succeeded' key", _get(r, "succeeded") is not None)
    _record("bulk_ops", "bulk_adjust response has 'failed' key",    _get(r, "failed") is not None)
    succeeded = _get(r, "succeeded") or []
    _record("bulk_ops", "bulk_adjust: 1 item succeeded", len(succeeded) == 1)

    # Partial-failure: one valid product + one non-existent product
    r = await _call_ok("bulk_ops",
                       "retailops_bulk_adjust_inventory (partial failure: 1 valid + 1 bad id)",
                       "retailops_bulk_adjust_inventory",
                       {"adjustments": [
                           {"product_id": pid,    "quantity": 5},
                           {"product_id": 999999, "quantity": 5},
                       ]})
    succeeded = _get(r, "succeeded") or []
    failed    = _get(r, "failed")    or []
    _record("bulk_ops", "partial bulk_adjust: 1 succeeded", len(succeeded) == 1)
    _record("bulk_ops", "partial bulk_adjust: 1 failed",    len(failed)    == 1)

    # Empty-list guard
    await _call_expect_error(
        "bulk_ops", "retailops_bulk_adjust_inventory (empty list →error)",
        "retailops_bulk_adjust_inventory",
        {"adjustments": []},
    )

    # ── Create 2 test orders for bulk lifecycle ────────────────────────────

    o1 = await _call_ok("bulk_ops", "create bulk test order 1",
                        "retailops_create_order", {
                            "customer_id": cid,
                            "items": [{"product_id": pid, "quantity": 1, "unit_price": "12.50"}],
                            "notes": f"Bulk test order A {_TS}",
                        })
    o2 = await _call_ok("bulk_ops", "create bulk test order 2",
                        "retailops_create_order", {
                            "customer_id": cid,
                            "items": [{"product_id": pid, "quantity": 1, "unit_price": "12.50"}],
                            "notes": f"Bulk test order B {_TS}",
                        })
    oid1 = _get(o1, "id")
    oid2 = _get(o2, "id")
    _created["bulk_order_ids"] = [x for x in [oid1, oid2] if x]

    if not oid1 or not oid2:
        _record("bulk_ops", "SKIP bulk lifecycle (order creation failed)", False)
        return

    # Submit both so they become Pending
    await _call_ok("bulk_ops", "submit bulk test order 1",
                   "retailops_submit_order", {"id": oid1})
    await _call_ok("bulk_ops", "submit bulk test order 2",
                   "retailops_submit_order", {"id": oid2})

    # ── bulk_confirm_orders ────────────────────────────────────────────────

    r = await _call_ok("bulk_ops", "retailops_bulk_confirm_orders (2 pending orders)",
                       "retailops_bulk_confirm_orders",
                       {"order_ids": [oid1, oid2]})
    succeeded = _get(r, "succeeded") or []
    _record("bulk_ops", "bulk_confirm: 2 orders confirmed", len(succeeded) == 2)

    # Partial-failure: try to re-confirm already-confirmed orders + non-existent
    r = await _call_ok("bulk_ops",
                       "retailops_bulk_confirm_orders (partial failure: wrong status + bad id)",
                       "retailops_bulk_confirm_orders",
                       {"order_ids": [oid1, 999999]})
    succeeded = _get(r, "succeeded") or []
    failed    = _get(r, "failed")    or []
    _record("bulk_ops", "partial bulk_confirm: 0 succeeded (already confirmed)", len(succeeded) == 0)
    _record("bulk_ops", "partial bulk_confirm: 2 failed",                        len(failed)    == 2)

    # Empty-list guard
    await _call_expect_error(
        "bulk_ops", "retailops_bulk_confirm_orders (empty list →error)",
        "retailops_bulk_confirm_orders",
        {"order_ids": []},
    )

    # ── Pay both orders to enable shipping ────────────────────────────────

    await _call_ok("bulk_ops", "pay bulk order 1 (full amount)",
                   "retailops_record_payment",
                   {"sales_order_id": oid1, "amount": "12.50", "payment_method": "cash"})
    await _call_ok("bulk_ops", "pay bulk order 2 (full amount)",
                   "retailops_record_payment",
                   {"sales_order_id": oid2, "amount": "12.50", "payment_method": "cash"})

    # Verify auto-transition to "paid" (full payment meets total_amount)
    r1 = await _call("retailops_get_order", {"id": oid1})
    r2 = await _call("retailops_get_order", {"id": oid2})
    _record("bulk_ops", "auto-transition: order 1 status='paid' after full payment",
            _get(r1, "status") == "paid")
    _record("bulk_ops", "auto-transition: order 2 status='paid' after full payment",
            _get(r2, "status") == "paid")

    # ── bulk_ship_orders ───────────────────────────────────────────────────

    r = await _call_ok("bulk_ops", "retailops_bulk_ship_orders (2 paid orders)",
                       "retailops_bulk_ship_orders",
                       {"order_ids": [oid1, oid2]})
    succeeded = _get(r, "succeeded") or []
    _record("bulk_ops", "bulk_ship: 2 orders shipped", len(succeeded) == 2)

    await _call_expect_error(
        "bulk_ops", "retailops_bulk_ship_orders (empty list →error)",
        "retailops_bulk_ship_orders",
        {"order_ids": []},
    )

    # ── bulk_deliver_orders ────────────────────────────────────────────────

    r = await _call_ok("bulk_ops", "retailops_bulk_deliver_orders (2 shipped orders)",
                       "retailops_bulk_deliver_orders",
                       {"order_ids": [oid1, oid2]})
    succeeded = _get(r, "succeeded") or []
    _record("bulk_ops", "bulk_deliver: 2 orders delivered", len(succeeded) == 2)

    await _call_expect_error(
        "bulk_ops", "retailops_bulk_deliver_orders (empty list →error)",
        "retailops_bulk_deliver_orders",
        {"order_ids": []},
    )


async def test_full_lifecycle():
    """
    Drives a single order through the complete standard path:
    Draft → Pending → Confirmed → Paid (auto-transition) → Shipped → Delivered

    Specifically validates the auto-pay transition (recording a payment that
    meets the full total_amount must flip the order to "paid" automatically).
    The original test_orders() only tests the cancel path.
    """
    print("\n--- Full Order Lifecycle (Draft→Delivered) ---")

    cid = _created.get("customer_id")
    pid = _created.get("product_id")
    if not cid or not pid:
        _record("lifecycle", "SKIP (no smoke customer/product)", False,
                "customer or product creation failed earlier")
        return

    # Create — single item, $12.50 total
    r = await _call_ok("lifecycle", "create order",
                       "retailops_create_order", {
                           "customer_id": cid,
                           "items": [{"product_id": pid, "quantity": 1, "unit_price": "12.50"}],
                           "notes": f"Lifecycle test {_TS}",
                       })
    oid = _get(r, "id")
    _record("lifecycle", "create returns id", oid is not None)
    if not oid:
        return

    # Submit
    r = await _call_ok("lifecycle", "submit →pending",
                       "retailops_submit_order", {"id": oid})
    _record("lifecycle", "status is pending", _get(r, "status") == "pending")

    # Confirm (deducts stock)
    r = await _call_ok("lifecycle", "confirm →confirmed",
                       "retailops_confirm_order", {"id": oid})
    _record("lifecycle", "status is confirmed", _get(r, "status") == "confirmed")

    # Record partial payment first, then full payment — verify auto-transition
    await _call_ok("lifecycle", "record partial payment ($5.00)",
                   "retailops_record_payment",
                   {"sales_order_id": oid, "amount": "5.00", "payment_method": "card"})
    r = await _call("retailops_get_order", {"id": oid})
    _record("lifecycle", "status still confirmed after partial payment",
            _get(r, "status") == "confirmed")

    # Pay the remaining balance (total_amount=12.50, already paid $5.00 → $7.50 due)
    await _call_ok("lifecycle", "record final payment ($7.50 — clears balance)",
                   "retailops_record_payment",
                   {"sales_order_id": oid, "amount": "7.50", "payment_method": "card"})
    r = await _call("retailops_get_order", {"id": oid})
    _record("lifecycle", "auto-transition: status='paid' after balance cleared",
            _get(r, "status") == "paid")
    _record("lifecycle", "amount_outstanding is 0.00 after full payment",
            str(_get(r, "amount_outstanding")) in ("0.00", "0", "0.0"))

    # Ship
    r = await _call_ok("lifecycle", "ship →shipped",
                       "retailops_ship_order", {"id": oid})
    _record("lifecycle", "status is shipped", _get(r, "status") == "shipped")

    # Deliver
    r = await _call_ok("lifecycle", "deliver →delivered",
                       "retailops_deliver_order", {"id": oid})
    _record("lifecycle", "status is delivered", _get(r, "status") == "delivered")

    # Verify final state
    r = await _call("retailops_get_order", {"id": oid})
    _record("lifecycle", "final status confirmed as delivered",
            _get(r, "status") == "delivered")


async def test_pagination():
    """
    Verifies pagination contract:
      - page_size=1 returns exactly 1 result per page
      - a 'next' link is present when count > 1
      - page=2 does not error
      - page_size clamped at 100 (server-side guard, mirrored client-side)
    """
    print("\n--- Pagination ---")

    # page_size=1 on a collection that has more than 1 record
    r = await _call_ok("pagination", "list_customers page_size=1",
                       "retailops_list_customers", {"page_size": 1})
    results = _get(r, "results") or []
    count   = _get(r, "count")   or 0
    _record("pagination", "page_size=1 → exactly 1 result in page", len(results) == 1)
    _record("pagination", "page_size=1 → next link present when count > 1",
            count <= 1 or _get(r, "next") is not None)

    # page=2 works without error
    r = await _call_ok("pagination", "list_customers page=2",
                       "retailops_list_customers", {"page": 2})
    _record("pagination", "page=2 returns without error", r is not None)

    # page_size=200: client clamps to 100 before sending; result count must be ≤ 100
    r = await _call_ok("pagination", "list_inventory_movements page_size=200 (clamped)",
                       "retailops_list_inventory_movements", {"page_size": 200})
    results = _get(r, "results") or []
    _record("pagination", "page_size=200 clamped → result count ≤ 100", len(results) <= 100)


# ══════════════════════════════════════════════════════════════════════════════
# Teardown — remove records created during the run
# ══════════════════════════════════════════════════════════════════════════════

async def teardown():
    print("\n--- Teardown ---")
    c = _created

    # Cannot delete the smoke-test product: inventory adjustments created
    # InventoryMovement records that reference it (on_delete=PROTECT).
    # Deactivate instead — correct system behaviour.
    if c.get("product_id"):
        try:
            await _call("retailops_update_product",
                        {"id": c["product_id"], "is_active": False})
            print(f"  Deactivated product {c['product_id']} (has inventory movements - cannot delete)")
        except Exception as e:
            print(f"  Could not deactivate product {c['product_id']}: {e}")

    # Delete the smoke-test category
    if c.get("category_id"):
        try:
            await _call("retailops_delete_category", {"id": c["category_id"]})
            print(f"  Deleted category {c['category_id']}")
        except Exception as e:
            print(f"  Could not delete category {c['category_id']}: {e}")

    # bulk_order_ids end in "delivered" — no cleanup needed (immutable final state)
    if c.get("bulk_order_ids"):
        print(f"  Bulk orders {c['bulk_order_ids']} left in 'delivered' state (final, no cleanup needed)")

    # Attempt to delete the smoke-test customer.
    # on_delete=PROTECT fires on ANY order row (cancelled, delivered, etc.), so this
    # will fail if any test orders remain linked to the customer. The except branch
    # handles that gracefully — leftover test data in a terminal state is harmless.
    if c.get("customer_id"):
        try:
            await _call("retailops_delete_customer", {"id": c["customer_id"]})
            print(f"  Deleted customer {c['customer_id']}")
        except Exception as e:
            print(f"  Could not delete customer {c['customer_id']}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# Main runner
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    print("=" * 70)
    print("RetailOps MCP Server — Full Tool Smoke Test")
    print("=" * 70)

    await test_dashboard()
    await test_auth()
    await test_customers()
    await test_categories()
    await test_products()
    await test_inventory()
    await test_orders()
    await test_payments()
    await test_users()
    await test_resources()
    await test_prompts()
    # ── Extended coverage ──────────────────────────────────────────────────
    await test_settings()
    await test_bulk_operations()
    await test_full_lifecycle()
    await test_pagination()
    # ──────────────────────────────────────────────────────────────────────
    await teardown()

    # ── Summary ──────────────────────────────────────────────────────────
    passed = sum(1 for r in _results if r[2] == "PASS")
    failed = sum(1 for r in _results if r[2] == "FAIL")
    total  = len(_results)

    print()
    print("=" * 70)
    print(f"Results: {passed}/{total} passed", end="")
    if failed:
        print(f"  |  {failed} FAILED:")
        for group, name, status, detail in _results:
            if status == "FAIL":
                print(f"    [{group}] {name}  -> {detail}")
    else:
        print("  — all tests passed.")
    print("=" * 70)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
