"""
api/views/mcp_skill.py
-----------------------
GET /api/v1/mcp-skill/

Returns a self-contained skill card that tells any AI agent or chat client
everything it needs to interact with the RetailOps MCP server.

No authentication required — this is a public capability descriptor.
Supports two formats:
  - JSON  (default, or ?format=json)
  - Markdown  (?format=markdown  or  Accept: text/markdown)
"""

from django.http import HttpResponse
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


# ── Skill data (static; derived from the live codebase) ──────────────────────

def _build_skill_card(request):
    base = request.build_absolute_uri('/api/v1')

    return {
        "schema_version": "1.0",
        "skill_id": "retailops-mcp",
        "display_name": "RetailOps MCP Server",
        "version": "1.0.0",
        "description": (
            "RetailOps is an internal retail and e-commerce order management system. "
            "This MCP server exposes its agent-facing API as 54 structured tools across 11 domains: "
            "Auth, Roles, Dashboard, Settings, Customers, Categories, Products, Inventory, "
            "Orders, Payments, and Users. Use these tools to manage the complete order "
            "lifecycle, record inventory movements, onboard customers, and monitor "
            "operational health."
        ),
        "api_base_url": base,
        "skill_url": request.build_absolute_uri('/api/v1/mcp-skill/'),

        # ── Connection ────────────────────────────────────────────────────
        "mcp_connection": {
            "transports": [
                {
                    "type": "stdio",
                    "description": "Default transport for Claude Desktop and local agents.",
                    "start_command": "python -m mcp_server.server",
                    "env_required": ["RETAILOPS_BASE_URL"],
                    "env_optional": ["RETAILOPS_API_TOKEN"],
                },
                {
                    "type": "sse",
                    "description": "HTTP Server-Sent Events — multi-client, suitable for remote agents.",
                    "start_command": "MCP_TRANSPORT=sse python -m mcp_server.server",
                    "remote_start_command": (
                        "MCP_TRANSPORT=sse MCP_HOST=0.0.0.0 MCP_AUTH_MODE=retailops-token "
                        "MCP_PUBLIC_BASE_URL=https://mcp.example.com MCP_ALLOWED_HOSTS=mcp.example.com "
                        "python -m mcp_server.server"
                    ),
                    "endpoint": "http://127.0.0.1:8001/sse",
                },
                {
                    "type": "streamable-http",
                    "description": "Stateless HTTP — production-ready with reverse proxy + TLS.",
                    "start_command": "MCP_TRANSPORT=streamable-http python -m mcp_server.server",
                    "remote_start_command": (
                        "MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_AUTH_MODE=retailops-token "
                        "MCP_PUBLIC_BASE_URL=https://mcp.example.com MCP_ALLOWED_HOSTS=mcp.example.com "
                        "python -m mcp_server.server"
                    ),
                    "endpoint": "http://127.0.0.1:8001/mcp",
                },
            ],
            "default_transport": "stdio",
            "configuration": {
                "RETAILOPS_BASE_URL": "Base URL of the Django API (default: http://127.0.0.1:8000/api/v1)",
                "RETAILOPS_API_TOKEN": "Local fallback RetailOps API token for stdio sessions",
                "RETAILOPS_TIMEOUT": "Request timeout in seconds (default: 30)",
                "MCP_AUTH_MODE": "local for stdio, or retailops-token for authenticated HTTP transports",
                "MCP_PUBLIC_BASE_URL": "Required https:// public MCP URL when binding SSE/streamable-http remotely",
                "MCP_ALLOWED_HOSTS": "Required comma-separated host allow-list for remote MCP transports",
                "MCP_ALLOWED_ORIGINS": "Optional comma-separated browser origin allow-list",
                "MCP_REQUIRED_SCOPES": "Optional comma-separated FastMCP scopes; defaults to retailops:access",
            },
        },

        # ── Authentication ────────────────────────────────────────────────
        "authentication": {
            "type": "retailops_token",
            "http_header": "REST API: Authorization: Token <token>; remote MCP HTTP: Authorization: Bearer <token>",
            "obtain_via_tool": {
                "tool": "retailops_login",
                "params": {"email": "<email>", "password": "<password>", "activate": "bool, default true"},
                "returns": {"token": "string", "role_name": "Admin|Manager|Staff", "activated": "bool"},
            },
            "obtain_via_http": {
                "method": "POST",
                "endpoint": f"{base}/auth/token/",
                "body": {"email": "<email>", "password": "<password>"},
            },
            "whoami_tool": "retailops_whoami",
            "revoke_via_tool": "retailops_logout",
            "note": (
                "stdio sessions may use retailops_login to activate a token in the local process. "
                "Remote SSE/streamable-http clients must send their own RetailOps API token as "
                "Authorization: Bearer <token>; non-loopback startup requires MCP_AUTH_MODE=retailops-token."
            ),
        },

        # ── Role hierarchy ────────────────────────────────────────────────
        "role_hierarchy": {
            "description": "Roles are hierarchical: Admin > Manager > Staff. "
                           "All roles can read data. Write access is role-gated.",
            "roles": {
                "Admin": [
                    "All tools including refund_order, user management",
                    "Update system settings (currency)",
                    "Deactivate / reactivate users",
                ],
                "Manager": [
                    "Confirm / cancel orders",
                    "Adjust inventory (single + bulk)",
                    "Create / update / delete products and categories",
                    "Update system settings",
                    "Bulk order transitions (confirm, ship, deliver)",
                ],
                "Staff": [
                    "Create / update / delete orders (Draft only)",
                    "Submit orders (Draft→Pending)",
                    "Ship and deliver orders",
                    "Record payments",
                ],
                "Any authenticated": [
                    "Read all data (list/get for all resources)",
                    "Dashboard summary",
                    "Get system settings",
                ],
            },
        },

        # ── Tools catalog ─────────────────────────────────────────────────
        "tools": {
            "auth": [
                {
                    "name": "retailops_login",
                    "role": "public",
                    "description": "Obtain a RetailOps token. In stdio, activate=true makes it the effective MCP token.",
                    "params": {"email": "string (required)", "password": "string (required)", "activate": "bool, default true"},
                },
                {
                    "name": "retailops_whoami",
                    "role": "any authenticated",
                    "description": "Return the RetailOps identity and token source currently used by this MCP request.",
                    "params": {},
                },
                {
                    "name": "retailops_logout",
                    "role": "any authenticated",
                    "description": "Safely revoke the current effective token; refuses to revoke RETAILOPS_API_TOKEN unless explicitly allowed.",
                    "params": {"revoke_env_token": "bool, default false"},
                },
            ],
            "roles": [
                {
                    "name": "retailops_list_roles",
                    "role": "Admin only",
                    "description": "List seeded role reference data for user creation and updates.",
                    "params": {"page": "int", "page_size": "int (max 100)"},
                },
                {
                    "name": "retailops_get_role",
                    "role": "Admin only",
                    "description": "Retrieve a single role by ID.",
                    "params": {"id": "int (required)"},
                },
            ],
            "dashboard": [
                {
                    "name": "retailops_get_dashboard",
                    "role": "any authenticated",
                    "description": "Returns orders_this_month, revenue_this_month, pending_payments_count, low_stock_count, recent_orders.",
                    "params": {},
                },
            ],
            "settings": [
                {
                    "name": "retailops_get_system_settings",
                    "role": "any authenticated",
                    "description": "Returns currency, secondary currency, OCR, receipt image, and retention settings.",
                    "params": {},
                },
                {
                    "name": "retailops_update_system_settings",
                    "role": "Manager+",
                    "description": "Partial-update currency, OCR/VEPay, receipt image requirement, and retention settings. At least one field required.",
                    "params": {
                        "currency_code": "string, optional (e.g. 'USD', 'EUR')",
                        "currency_symbol": "string, optional (e.g. '$', '€', 'Bs')",
                        "decimal_places": "int, optional (0–4)",
                        "secondary_*": "secondary currency fields, optional",
                        "ocr_enabled": "bool, optional",
                        "ocr_provider": "string, optional (currently 'vepay')",
                        "ocr_base_url": "URL, optional",
                        "ocr_api_key": "string, optional; empty string clears it",
                        "ocr_timeout_seconds": "positive int, optional",
                        "ocr_max_file_mb": "positive int, optional",
                        "ocr_strict_amount": "bool, optional",
                        "ocr_require_complete": "bool, optional",
                        "ocr_enabled_methods": "list of 'mobile_payment'|'bank_transfer', optional",
                        "receipt_image_required_for_receipt_methods": "bool, optional",
                        "delete_receipt_image_after_days": "positive int, optional",
                    },
                },
            ],
            "customers": [
                {
                    "name": "retailops_list_customers",
                    "role": "any authenticated",
                    "description": "Paginated customer list. Searches name and email.",
                    "params": {"search": "string, optional", "page": "int", "page_size": "int (max 100)"},
                },
                {
                    "name": "retailops_get_customer",
                    "role": "any authenticated",
                    "description": "Full customer record including address.",
                    "params": {"id": "int (required)"},
                },
                {
                    "name": "retailops_create_customer",
                    "role": "any authenticated",
                    "description": "Create a customer. email must be unique.",
                    "params": {
                        "first_name": "string (required)", "last_name": "string (required)",
                        "email": "string (required, unique)",
                        "address_line1": "string (required)", "city": "string (required)",
                        "state": "string (required)", "postal_code": "string (required)",
                        "country": "string (default: 'United States')",
                        "phone": "string, optional", "address_line2": "string, optional",
                    },
                },
                {
                    "name": "retailops_update_customer",
                    "role": "any authenticated",
                    "description": "Partial update — only provided fields are changed.",
                    "params": {"id": "int (required)", "...": "any customer field"},
                },
                {
                    "name": "retailops_delete_customer",
                    "role": "any authenticated",
                    "description": "Hard delete. Returns 409 Conflict if the customer has any orders.",
                    "params": {"id": "int (required)"},
                },
            ],
            "categories": [
                {
                    "name": "retailops_list_categories",
                    "role": "any authenticated",
                    "description": "Paginated list including hierarchy and display_name (ancestry path).",
                    "params": {"page": "int", "page_size": "int (max 100)"},
                },
                {
                    "name": "retailops_get_category",
                    "role": "any authenticated",
                    "description": "Full category record with subcategories list.",
                    "params": {"id": "int (required)"},
                },
                {
                    "name": "retailops_create_category",
                    "role": "Manager+",
                    "description": "Create a category. name must be unique.",
                    "params": {"name": "string (required)", "description": "string, optional", "parent_category": "int, optional"},
                },
                {
                    "name": "retailops_update_category",
                    "role": "Manager+",
                    "description": "Partial update. Cannot set parent_category to itself.",
                    "params": {"id": "int (required)", "...": "any category field"},
                },
                {
                    "name": "retailops_delete_category",
                    "role": "Manager+",
                    "description": "Delete. Returns 409 Conflict if any products are assigned to it.",
                    "params": {"id": "int (required)"},
                },
            ],
            "products": [
                {
                    "name": "retailops_list_products",
                    "role": "any authenticated",
                    "description": "Paginated product list with image metadata. stock filter uses real-time inventory aggregation.",
                    "params": {
                        "search": "string, optional",
                        "category": "int, optional",
                        "is_active": "bool, optional",
                        "stock": "'out'|'low'|'ok', optional",
                        "ordering": "string, optional",
                        "page": "int", "page_size": "int (max 100)",
                    },
                },
                {
                    "name": "retailops_get_product",
                    "role": "any authenticated",
                    "description": "Full product record with live stock and image fields: image, external_image_url, primary_image_url, has_image.",
                    "params": {"id": "int (required)"},
                },
                {
                    "name": "retailops_create_product",
                    "role": "Manager+",
                    "description": "Create a product. Active products require image_path or external_image_url.",
                    "params": {
                        "sku": "string (required, unique, immutable)",
                        "name": "string (required)",
                        "category_id": "int (required)",
                        "unit_price": "decimal string (required, e.g. '9.99')",
                        "unit_of_measure": "'piece'|'kg'|'liter'|'meter'|'box'|'pack' (required)",
                        "description": "string, optional",
                        "low_stock_threshold": "int (default: 10)",
                        "is_active": "bool, optional; defaults active only when an image source exists",
                        "external_image_url": "string URL, optional",
                        "image_path": "local file path on MCP server host, optional",
                    },
                },
                {
                    "name": "retailops_update_product",
                    "role": "Manager+",
                    "description": "Partial update. SKU cannot be changed. Supports external_image_url, image_path, and clear_image.",
                    "params": {"id": "int (required)", "...": "any product field except sku", "image_path": "local file path, optional", "clear_image": "bool, optional"},
                },
                {
                    "name": "retailops_delete_product",
                    "role": "Manager+",
                    "description": "Delete. Returns 409 Conflict if any inventory movements reference it.",
                    "params": {"id": "int (required)"},
                },
                {
                    "name": "retailops_get_product_movements",
                    "role": "any authenticated",
                    "description": "Paginated audit log of all stock changes for a product.",
                    "params": {"id": "int (required)", "page": "int", "page_size": "int (max 100)"},
                },
            ],
            "inventory": [
                {
                    "name": "retailops_list_inventory_movements",
                    "role": "any authenticated",
                    "description": "Append-only movement log across all products. Supports date/type filters.",
                    "params": {
                        "product": "int, optional",
                        "movement_type": "'sale'|'purchase'|'adjustment'|'return', optional",
                        "reference_type": "'SalesOrder'|'PurchaseOrder'|'ManualAdjustment'|'Return', optional",
                        "date_from": "YYYY-MM-DD, optional",
                        "date_to": "YYYY-MM-DD, optional",
                        "page": "int", "page_size": "int (max 100)",
                    },
                },
                {
                    "name": "retailops_get_inventory_movement",
                    "role": "any authenticated",
                    "description": "Single movement record with product details and created_by user.",
                    "params": {"id": "int (required)"},
                },
                {
                    "name": "retailops_adjust_inventory",
                    "role": "Manager+",
                    "description": "Record a manual stock adjustment (movement_type='adjustment'). quantity must be non-zero.",
                    "params": {
                        "product_id": "int (required)",
                        "quantity": "int (required, non-zero; positive adds, negative removes)",
                        "notes": "string, optional (recommended for audit trail)",
                    },
                },
                {
                    "name": "retailops_bulk_adjust_inventory",
                    "role": "Manager+",
                    "description": "Adjust multiple products. Partial success: each item is independent. Returns {succeeded, failed}.",
                    "params": {
                        "adjustments": "list (required, non-empty) of {product_id, quantity, notes?}",
                    },
                },
            ],
            "orders": [
                {
                    "name": "retailops_list_orders",
                    "role": "any authenticated",
                    "description": "Paginated order list with status and date filters.",
                    "params": {
                        "customer": "int, optional",
                        "status": "'draft'|'pending'|'confirmed'|'paid'|'shipped'|'delivered'|'cancelled'|'refunded', optional",
                        "date_from": "YYYY-MM-DD, optional",
                        "date_to": "YYYY-MM-DD, optional",
                        "page": "int", "page_size": "int (max 100)",
                    },
                },
                {
                    "name": "retailops_get_order",
                    "role": "any authenticated",
                    "description": "Full order: header, line items, payment history, computed amount_paid and amount_outstanding.",
                    "params": {"id": "int (required)"},
                },
                {
                    "name": "retailops_create_order",
                    "role": "Staff+",
                    "description": "Create a Draft order. items must be non-empty. Does NOT affect stock.",
                    "params": {
                        "customer_id": "int (required)",
                        "items": "list (required, ≥1) of {product_id, quantity, unit_price?, tax_rate?}",
                        "discount_amount": "decimal string, optional",
                        "tax_amount": "decimal string, optional",
                        "notes": "string, optional",
                    },
                },
                {
                    "name": "retailops_update_order",
                    "role": "Staff+",
                    "description": "Edit a Draft or Pending order. Providing items REPLACES all line items.",
                    "params": {
                        "id": "int (required)",
                        "items": "list, optional (full replacement if provided)",
                        "discount_amount": "decimal string, optional",
                        "tax_amount": "decimal string, optional",
                        "notes": "string, optional",
                    },
                },
                {
                    "name": "retailops_delete_order",
                    "role": "Staff+",
                    "description": "Permanently delete a Draft order. Irreversible.",
                    "params": {"id": "int (required)"},
                },
                {
                    "name": "retailops_submit_order",
                    "role": "Staff+",
                    "description": "Draft → Pending. Signals ready for manager review. No inventory change.",
                    "params": {"id": "int (required)"},
                },
                {
                    "name": "retailops_confirm_order",
                    "role": "Manager+",
                    "description": "Pending → Confirmed. DEDUCTS STOCK (one negative InventoryMovement per line item). Atomic — rolls back on failure.",
                    "params": {"id": "int (required)"},
                },
                {
                    "name": "retailops_cancel_order",
                    "role": "Manager+",
                    "description": "Confirmed → Cancelled. RESTORES STOCK. Cannot cancel after payment — use refund instead.",
                    "params": {"id": "int (required)"},
                },
                {
                    "name": "retailops_ship_order",
                    "role": "Staff+",
                    "description": "Paid → Shipped. No inventory change.",
                    "params": {"id": "int (required)"},
                },
                {
                    "name": "retailops_deliver_order",
                    "role": "Staff+",
                    "description": "Shipped → Delivered. Final standard lifecycle step.",
                    "params": {"id": "int (required)"},
                },
                {
                    "name": "retailops_refund_order",
                    "role": "Admin only",
                    "description": "Paid → Refunded. RESTORES STOCK. Payment records are NOT deleted (immutable).",
                    "params": {"id": "int (required)"},
                },
                {
                    "name": "retailops_record_payment",
                    "role": "any authenticated",
                    "description": "Record a payment against a Confirmed order. AUTO-TRANSITIONS to Paid when total_paid ≥ total_amount.",
                    "params": {
                        "sales_order_id": "int (required)",
                        "amount": "decimal string (required, > 0)",
                        "payment_method": "'cash'|'mobile_payment'|'bank_transfer'|'card'|'check'|'other' (required)",
                        "reference_number": "string, optional",
                        "notes": "string, optional",
                        "status": "'pending_review'|'confirmed', optional",
                        "transaction_key": "string, optional",
                        "origin_phone": "string, optional",
                        "origin_bank": "string, optional",
                        "recipient_bank": "string, optional",
                        "recipient_account": "string, optional",
                        "ocr_receipt_data": "object, optional",
                        "receipt_image_path": "local receipt image path, optional",
                    },
                },
                {
                    "name": "retailops_bulk_confirm_orders",
                    "role": "Manager+",
                    "description": "Confirm multiple Pending orders. Partial success. Returns {succeeded, failed}.",
                    "params": {"order_ids": "list of int (required, non-empty)"},
                },
                {
                    "name": "retailops_bulk_ship_orders",
                    "role": "Manager+",
                    "description": "Ship multiple Paid orders. Partial success. Returns {succeeded, failed}.",
                    "params": {"order_ids": "list of int (required, non-empty)"},
                },
                {
                    "name": "retailops_bulk_deliver_orders",
                    "role": "Manager+",
                    "description": "Deliver multiple Shipped orders. Partial success. Returns {succeeded, failed}.",
                    "params": {"order_ids": "list of int (required, non-empty)"},
                },
            ],
            "payments": [
                {
                    "name": "retailops_list_payments",
                    "role": "any authenticated",
                    "description": "Paginated payment ledger. Immutable records — never edited or deleted.",
                    "params": {
                        "sales_order": "int, optional",
                        "payment_method": "string, optional",
                        "date_from": "YYYY-MM-DD, optional",
                        "date_to": "YYYY-MM-DD, optional",
                        "page": "int", "page_size": "int (max 100)",
                    },
                },
                {
                    "name": "retailops_get_payment",
                    "role": "any authenticated",
                    "description": "Single payment with nested order summary and recorded_by user.",
                    "params": {"id": "int (required)"},
                },
                {
                    "name": "retailops_check_receipt_ocr_health",
                    "role": "Manager+",
                    "description": "Check configured OCR/VEPay provider connectivity.",
                    "params": {},
                },
                {
                    "name": "retailops_verify_receipt",
                    "role": "Manager+",
                    "description": "Upload a local receipt image and verify OCR fields without creating a payment.",
                    "params": {
                        "image_path": "local file path (required)",
                        "payment_method": "'mobile_payment'|'bank_transfer' (required)",
                        "sales_order_id": "int, optional",
                        "expected_amount_usd": "decimal string, required when sales_order_id omitted",
                        "expected_reference": "string, optional",
                        "expected_paid_on": "YYYY-MM-DD, optional",
                        "expected_origin_bank": "string, optional",
                    },
                },
            ],
            "users": [
                {
                    "name": "retailops_list_users",
                    "role": "Admin only",
                    "description": "All users with their roles.",
                    "params": {"page": "int", "page_size": "int (max 100)"},
                },
                {
                    "name": "retailops_get_user",
                    "role": "Admin only",
                    "description": "Single user record (password not returned).",
                    "params": {"id": "int (required)"},
                },
                {
                    "name": "retailops_create_user",
                    "role": "Admin only",
                    "description": "Create a user with a role. password min 8 chars.",
                    "params": {
                        "email": "string (required, unique)",
                        "password": "string (required, ≥8 chars)",
                        "role": "int (required: 1=Admin, 2=Manager, 3=Staff)",
                        "first_name": "string, optional", "last_name": "string, optional",
                        "is_active": "bool (default: true)",
                    },
                },
                {
                    "name": "retailops_update_user",
                    "role": "Admin only",
                    "description": "Partial update of profile fields (no password — use change_password).",
                    "params": {
                        "id": "int (required)",
                        "email": "string, optional", "first_name": "string, optional",
                        "last_name": "string, optional", "role": "int, optional",
                        "is_active": "bool, optional",
                    },
                },
                {
                    "name": "retailops_change_password",
                    "role": "Admin only",
                    "description": "Admin override — old password not required. new_password and confirm_password must match.",
                    "params": {
                        "id": "int (required)",
                        "new_password": "string (required)",
                        "confirm_password": "string (required, must match new_password)",
                    },
                },
                {
                    "name": "retailops_deactivate_user",
                    "role": "Admin only",
                    "description": "Soft-delete (is_active=False). Cannot self-deactivate.",
                    "params": {"id": "int (required)"},
                },
                {
                    "name": "retailops_reactivate_user",
                    "role": "Admin only",
                    "description": "Restore is_active=True.",
                    "params": {"id": "int (required)"},
                },
            ],
        },

        # ── Resources (read-only URI browsing) ────────────────────────────
        "resources": [
            {"uri": "retailops://dashboard",                 "description": "Summary counts and recent activity"},
            {"uri": "retailops://settings",                  "description": "System settings including currency and OCR"},
            {"uri": "retailops://roles",                     "description": "First 25 roles (Admin token required)"},
            {"uri": "retailops://customers",                 "description": "First 25 customers"},
            {"uri": "retailops://customers/{id}",            "description": "Single customer"},
            {"uri": "retailops://products",                  "description": "First 25 products with live stock"},
            {"uri": "retailops://products/{id}",             "description": "Single product with computed stock"},
            {"uri": "retailops://products/{id}/movements",   "description": "First 25 inventory movements for a product"},
            {"uri": "retailops://categories",                "description": "First 25 categories with hierarchy"},
            {"uri": "retailops://orders",                    "description": "Most recent 25 orders"},
            {"uri": "retailops://orders/{id}",               "description": "Single order with line items and payments"},
            {"uri": "retailops://payments",                  "description": "Most recent 25 payments"},
            {"uri": "retailops://inventory",                 "description": "Most recent 25 movement records"},
            {"uri": "retailops://users",                     "description": "First 25 users (Admin token required)"},
        ],

        # ── Guided workflow prompts ───────────────────────────────────────
        "workflows": [
            {
                "name": "retailops_create_order_workflow",
                "description": "End-to-end order creation: customer lookup/creation → product selection → order creation → submission.",
                "params": {
                    "customer_hint": "string, optional — name or email fragment to search",
                    "product_hints": "string, optional — comma-separated SKUs or product names",
                },
                "steps": [
                    "Search for customer by hint; create if not found",
                    "Resolve product IDs from hints or ask user to select",
                    "Create order (Draft) with confirmed customer and product IDs",
                    "Present order summary; ask for confirmation",
                    "Submit order (Draft → Pending)",
                    "Notify user of order_number and next steps",
                ],
            },
            {
                "name": "retailops_process_payment_workflow",
                "description": "Record payment against a Confirmed order, including OCR receipt verification for mobile payment and bank transfer.",
                "params": {"order_id": "int (required)"},
                "steps": [
                    "Retrieve order; verify status=confirmed and amount_outstanding > 0",
                    "Present total_amount, amount_paid, amount_outstanding to user",
                    "Collect payment_method, amount, reference, receipt fields, and receipt image path",
                    "For mobile_payment/bank_transfer, check settings and OCR health, then verify the receipt",
                    "Record payment with receipt metadata and OCR payload when verification passes",
                    "Report new balance or confirmation of full payment",
                ],
            },
            {
                "name": "retailops_cancel_or_refund_workflow",
                "description": "Status-aware order reversal: cancel (pre-payment) or refund (post-payment).",
                "params": {"order_id": "int (required)"},
                "steps": [
                    "Retrieve order; inspect status",
                    "Branch: draft/pending → delete; confirmed → cancel; paid → refund; "
                    "shipped/delivered → inform agent that manual stock adjustment is the only option",
                ],
            },
            {
                "name": "retailops_stock_check_workflow",
                "description": "Full inventory health check: identify zero-stock and low-stock products.",
                "params": {},
                "steps": [
                    "Retrieve dashboard (low_stock_count)",
                    "List products with stock=out",
                    "List products with stock=low",
                    "Optionally record stock receipts via retailops_adjust_inventory",
                    "Summarise findings",
                ],
            },
            {
                "name": "retailops_onboard_customer_workflow",
                "description": "Onboard a new customer: duplicate check → create → optional first order.",
                "params": {},
                "steps": [
                    "Search existing customers by name/email to detect duplicates",
                    "Collect required fields (name, email, address)",
                    "Create customer",
                    "Optionally invoke retailops_create_order_workflow for their first order",
                ],
            },
        ],

        # ── Order lifecycle ───────────────────────────────────────────────
        "order_lifecycle": {
            "states": ["draft", "pending", "confirmed", "paid", "shipped", "delivered", "cancelled", "refunded"],
            "transitions": [
                {"from": "draft",     "to": "pending",   "tool": "retailops_submit_order",  "role": "Staff+",   "inventory": "none"},
                {"from": "pending",   "to": "confirmed", "tool": "retailops_confirm_order", "role": "Manager+", "inventory": "DEDUCT per line item"},
                {"from": "confirmed", "to": "paid",      "tool": "retailops_record_payment","role": "any",      "inventory": "none",
                 "note": "Auto-transition when total_paid >= total_amount"},
                {"from": "paid",      "to": "shipped",   "tool": "retailops_ship_order",    "role": "Staff+",   "inventory": "none"},
                {"from": "shipped",   "to": "delivered", "tool": "retailops_deliver_order", "role": "Staff+",   "inventory": "none"},
                {"from": "confirmed", "to": "cancelled", "tool": "retailops_cancel_order",  "role": "Manager+", "inventory": "RESTORE per line item"},
                {"from": "paid",      "to": "refunded",  "tool": "retailops_refund_order",  "role": "Admin",    "inventory": "RESTORE per line item",
                 "note": "Payment records are NOT deleted"},
            ],
            "key_rules": [
                "Stock is ONLY deducted at confirm_order — not at create or submit.",
                "Stock is restored at cancel_order and refund_order.",
                "Once an order is paid, it can only be refunded (Admin only), never cancelled.",
                "Delivered and refunded orders are terminal — no further transitions.",
                "Payments are immutable once recorded.",
            ],
        },

        # ── Critical constraints ──────────────────────────────────────────
        "constraints": {
            "foreign_key_guards": {
                "delete_customer": "Fails (409) if customer has any orders.",
                "delete_category": "Fails (409) if any products are assigned to it.",
                "delete_product":  "Fails (409) if any inventory movements reference it — deactivate instead.",
                "delete_order":    "Only allowed in Draft status.",
            },
            "immutability": {
                "payments":            "Payment records cannot be edited or deleted.",
                "inventory_movements": "Movement records are append-only — never edited or deleted.",
                "product_sku":         "SKU cannot be changed after creation.",
                "order_number":        "order_number is auto-generated (SO-YYYYMMDD-XXXX) and immutable.",
                "payment_number":      "payment_number is auto-generated (PAY-YYYYMMDD-XXXX) and immutable.",
            },
            "validation": {
                "adjust_inventory_quantity": "Must be non-zero.",
                "create_order_items":        "Must contain at least 1 item.",
                "bulk_order_ids":            "Must be a non-empty list.",
                "bulk_adjustments":          "Must be a non-empty list.",
                "update_system_settings":    "At least one field must be provided.",
                "password_minimum_length":   "8 characters.",
            },
            "soft_deletes": "Users are soft-deleted (is_active=False), not hard-deleted.",
            "singleton": "SystemSettings always has exactly one row (pk=1); use get/update, never create/delete.",
        },

        # ── Error codes ───────────────────────────────────────────────────
        "errors": {
            "401_authentication_failed": (
                "Token is invalid or expired. In stdio, use retailops_login or refresh "
                "RETAILOPS_API_TOKEN. In remote HTTP MCP, reconnect with a valid "
                "Authorization: Bearer <RetailOps API token> header."
            ),
            "403_permission_denied":     "Insufficient role. Check the role_hierarchy and use an account with the required role.",
            "404_not_found":             "Resource does not exist. Verify the ID from a prior list_* or create_* call.",
            "409_conflict":              "Record is blocked by dependent records. Delete or reassign dependents first.",
            "400_validation_error":      "Field-level validation failed. The error response includes per-field details.",
            "429_rate_limited":          "Too many requests. Authenticated users: 600/min. Login endpoint: 20/min.",
            "500_server_error":          "Internal server error. Check Django dev server logs.",
        },
    }


# ── Markdown renderer ─────────────────────────────────────────────────────────

def _card_to_markdown(card: dict) -> str:
    lines = []
    a = lines.append

    a(f"# {card['display_name']} — MCP Skill Card\n")
    a(f"**Skill ID:** `{card['skill_id']}`  ")
    a(f"**Version:** {card['version']}  ")
    a(f"**API Base:** {card['api_base_url']}\n")
    a(card['description'] + "\n")

    # Connection
    a("## Connection\n")
    for t in card['mcp_connection']['transports']:
        a(f"**{t['type'].upper()}** — {t['description']}  ")
        a(f"Start: `{t['start_command']}`  ")
        if 'remote_start_command' in t:
            a(f"Remote start: `{t['remote_start_command']}`  ")
        if 'endpoint' in t:
            a(f"Endpoint: `{t['endpoint']}`")
        a("")

    # Auth
    a("## Authentication\n")
    auth = card['authentication']
    a(f"Header: `{auth['http_header']}`\n")
    a(f"Obtain via tool: `{auth['obtain_via_tool']['tool']}(email, password, activate=True)` -> returns `token` and `activated`  ")
    a(f"Current identity tool: `{auth['whoami_tool']}`  ")
    a(f"Obtain via HTTP: `POST {auth['obtain_via_http']['endpoint']}`  ")
    a(f"> {auth['note']}\n")

    # Role hierarchy
    a("## Role Hierarchy\n")
    a(card['role_hierarchy']['description'] + "\n")
    for role, perms in card['role_hierarchy']['roles'].items():
        a(f"**{role}:** {', '.join(perms)}")
    a("")

    # Tools
    a("## Tools\n")
    for domain, tools in card['tools'].items():
        a(f"### {domain.title()}\n")
        for tool in tools:
            params_str = ", ".join(
                f"`{k}`: {v}" for k, v in tool.get('params', {}).items()
                if k != "..."
            )
            a(f"**`{tool['name']}`** _(role: {tool['role']})_  ")
            a(f"{tool['description']}  ")
            if params_str:
                a(f"Params: {params_str}")
            a("")

    # Resources
    a("## Resources (read-only URI browsing)\n")
    a("| URI | Description |")
    a("|-----|-------------|")
    for r in card['resources']:
        a(f"| `{r['uri']}` | {r['description']} |")
    a("")

    # Workflows
    a("## Guided Workflows (MCP Prompts)\n")
    for wf in card['workflows']:
        a(f"### `{wf['name']}`\n")
        a(wf['description'] + "\n")
        if wf.get('params'):
            params_str = ", ".join(f"`{k}`: {v}" for k, v in wf['params'].items())
            a(f"**Params:** {params_str}\n")
        for i, step in enumerate(wf['steps'], 1):
            a(f"{i}. {step}")
        a("")

    # Order lifecycle
    a("## Order Lifecycle\n")
    lc = card['order_lifecycle']
    a(f"States: {' → '.join(lc['states'][:6])} / cancelled / refunded\n")
    a("| From | To | Tool | Role | Inventory |")
    a("|------|----|------|------|-----------|")
    for t in lc['transitions']:
        note = f" _{t['note']}_" if 'note' in t else ""
        a(f"| {t['from']} | {t['to']} | `{t['tool']}` | {t['role']} | {t['inventory']}{note} |")
    a("")
    a("**Key rules:**")
    for rule in lc['key_rules']:
        a(f"- {rule}")
    a("")

    # Constraints
    a("## Constraints\n")
    c = card['constraints']
    a("**Foreign-key guards (409 Conflict):**")
    for k, v in c['foreign_key_guards'].items():
        a(f"- `{k}`: {v}")
    a("")
    a("**Immutability:**")
    for k, v in c['immutability'].items():
        a(f"- `{k}`: {v}")
    a("")
    a("**Validation:**")
    for k, v in c['validation'].items():
        a(f"- `{k}`: {v}")
    a("")

    # Errors
    a("## Error Reference\n")
    a("| Code | Meaning & Recovery |")
    a("|------|--------------------|")
    for code, msg in card['errors'].items():
        a(f"| `{code}` | {msg} |")
    a("")

    return "\n".join(lines)


# ── View ──────────────────────────────────────────────────────────────────────

class MCPSkillView(APIView):
    """
    GET /api/v1/mcp-skill/

    Returns the RetailOps MCP skill card. No authentication required.
    Supports JSON (default) and Markdown (?format=markdown or Accept: text/markdown).
    """
    permission_classes = [AllowAny]
    # Explicitly bypass the DRF content-negotiation for plain text responses.
    # JSON responses still go through the normal renderer stack.

    def get(self, request, *args, **kwargs):
        card = _build_skill_card(request)
        fmt = request.query_params.get("format", "").lower()
        accept = request.META.get("HTTP_ACCEPT", "")

        if fmt == "markdown" or "text/markdown" in accept:
            md = _card_to_markdown(card)
            return HttpResponse(md, content_type="text/markdown; charset=utf-8")

        return Response(card)
