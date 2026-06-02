# CHANGELOG-07 — MCP Testing, Skill Card, and Documentation Pass

**Session date:** 2026-04-12  
**Scope:** Four distinct deliverables across this session: (1) CLAUDE.md backfill from Sessions 5–6, (2) a gap-closing MCP integration test suite, (3) a new public MCP skill card endpoint, and (4) a comprehensive documentation update pass over `MCP_GUIDE.md` and `API_GUIDE.md`.

---

## Overview

This session performed no changes to the Django application's core models, views, or API logic. All work was in the testing, tooling, and documentation layers. The primary goals were:

1. **Bring CLAUDE.md current** with changes from Sessions 5 and 6 that had not been reflected in the project instructions file.
2. **Close test coverage gaps** in `test_mcp_tools.py` — the four biggest untested areas were bulk operations, the system settings tools, the auto-pay transition, and pagination edge cases.
3. **Create a discoverable skill card** at `GET /api/v1/mcp-skill/` so any AI agent or chat client can bootstrap itself against the MCP server from a single HTTP request.
4. **Correct and extend both guide files** — `MCP_GUIDE.md` had accumulated stale counts and a wrong resource entry; `API_GUIDE.md` was missing six endpoint families added in Sessions 5 and 6.

---

## 1 — CLAUDE.md Backfill

The `/init` command triggered a review of the existing `CLAUDE.md` against the actual codebase. Six categories of stale or missing content were found and fixed.

### Changes

**Project Overview**  
Removed hardcoded template/view counts from the description (they were stale and would become stale again). Updated the changelog reference from "see CHANGELOG.md through CHANGELOG-04.md" to "through CHANGELOG-06.md".

**Project Structure section**  
- Added `core/middleware.py` — `RegionalMiddleware`, which activates per-user timezone and language on every authenticated request.
- Added `core/templatetags/regional.py` — the `|currency` template filter.
- Updated entity count from 9 → 10 (added `SystemSettings`).
- Updated template count from 14 → 15 (added `settings.html`).
- Updated MCP tools directory count from 9 → 10 modules (added `settings.py`).
- Updated `api/urls.py` description to mention the settings endpoint.
- Added `api/views/settings.py` to the views directory description.

**Data Model section**  
- Updated heading from "9 entities" → "10 entities".
- Added `timezone` (IANA string, default `UTC`) and `language` (default `en`) fields to the `User` model description.
- Added `SystemSettings` singleton description, including the `get()` classmethod and its role in the `|currency` filter and order form context.

**Templates section**  
Added a paragraph on the `|currency` filter convention:
> Templates that display monetary values load `{% load regional %}` and use the `|currency` filter. Never render raw `$` prefixes — always pipe through `|currency`. For JS live-recalculation in `order_detail.html`, use the injected `CURRENCY_SYMBOL` / `CURRENCY_DECIMALS` constants and the `fmtCurrency()` helper.

**REST API resources table**  
Added the `settings/` row:
> `GET` any authenticated — returns `{currency_code, currency_symbol, decimal_places}`; `PATCH` Manager+ — partial update of currency fields.

Added the `mcp-skill/` row (new this session):
> `GET` public (no auth) — returns the MCP skill card; `?format=markdown` for plain-text version.

**Known Gaps table**  
Added two rows that had been documented in CHANGELOG-06 but not reflected in CLAUDE.md:
- **i18n** — `RegionalMiddleware` is active but no `.po` translation files exist yet.
- **Currency validation** — `currency_code` accepts any 1–3 character string with no ISO 4217 enforcement.

**Seeded Credentials table**  
Added `norole@retailops.local` (manually created during Session 5 for 403 verification; no seeded password).

---

## 2 — Extended MCP Integration Test Suite (`test_mcp_tools.py`)

### Problem

The existing smoke test exercised all 43 tools that were present at Session 4, but four categories of behaviour added in Sessions 5 and 6 were completely untested:

| Gap | What was missing |
|-----|-----------------|
| **Bulk operations** | `retailops_bulk_adjust_inventory`, `retailops_bulk_confirm_orders`, `retailops_bulk_ship_orders`, `retailops_bulk_deliver_orders` — never called |
| **Settings tools** | `retailops_get_system_settings`, `retailops_update_system_settings` — never called |
| **Auto-pay transition** | Recording a payment that meets the full `total_amount` should flip the order to `"paid"` in the same transaction — never verified |
| **Pagination edge cases** | `page_size=1`, `page=2`, `page_size` clamped to 100 — never exercised |

Additionally, the partial-success response shape (`{succeeded, failed}`) of all bulk endpoints was untested, and the `retailops_update_system_settings` no-op guard (calling with zero fields) was untested.

### Solution — four new test groups

#### `test_settings()` (6 assertions)

1. `retailops_get_system_settings` returns `currency_code`, `currency_symbol`, and `decimal_places` keys.
2. `retailops_update_system_settings(decimal_places=3)` returns the updated value.
3. A subsequent `get` confirms the update persisted (round-trip).
4. Calling `retailops_update_system_settings` with no arguments raises a `ValueError` (MCP-layer no-op guard).
5. Original values are restored at the end so downstream tests see unmodified settings.

#### `test_bulk_operations()` (~20 assertions)

**`retailops_bulk_adjust_inventory`:**
- Single-item success: response has `succeeded` and `failed` keys; `succeeded` contains 1 item.
- Partial failure (1 valid product + product_id=999999): `len(succeeded)==1`, `len(failed)==1`.
- Empty list guard raises `ValueError`.

**`retailops_bulk_confirm_orders`:**
- Creates 2 fresh test orders, submits both, then bulk-confirms both: `len(succeeded)==2`.
- Re-confirming already-confirmed orders + a non-existent ID: `len(succeeded)==0`, `len(failed)==2`.
- Empty list guard raises `ValueError`.

**Auto-pay transition (incidentally tested here):**
- Records full payment (`amount="12.50"`) against each confirmed order.
- Verifies `status == "paid"` on both orders immediately after (auto-transition confirmed).

**`retailops_bulk_ship_orders`:**
- Ships both paid orders: `len(succeeded)==2`.
- Empty list guard raises `ValueError`.

**`retailops_bulk_deliver_orders`:**
- Delivers both shipped orders: `len(succeeded)==2`.
- Empty list guard raises `ValueError`.

#### `test_full_lifecycle()` (9 assertions)

Drives a single order through the complete standard path end-to-end without using the cancel branch (which was already covered in `test_orders()`):

1. Create order (Draft).
2. Submit → status `pending`.
3. Confirm → status `confirmed`.
4. Record **partial** payment ($5.00 of $12.50) → status still `confirmed`.
5. Record **final** payment ($7.50, clears balance) → status auto-transitions to `"paid"`.
6. Verify `amount_outstanding` is `"0.00"`.
7. Ship → status `shipped`.
8. Deliver → status `delivered`.
9. Final `get_order` call confirms terminal state.

This is the first test covering the `ship` and `deliver` transitions and the two-payment auto-transition scenario.

#### `test_pagination()` (4 assertions)

1. `list_customers(page_size=1)` returns exactly 1 result.
2. When `count > 1`, the `next` link is present.
3. `list_customers(page=2)` returns without error.
4. `list_inventory_movements(page_size=200)` returns ≤ 100 results (client-side `min(page_size, 100)` confirmed).

### Other changes

**`teardown()` update**  
The comment on the customer deletion was wrong — it said "order was cancelled, FK guard should be clear." The `on_delete=PROTECT` guard fires on *any* order row regardless of status, so the deletion will usually fail (the customer now has delivered and cancelled orders against it). The comment was corrected to reflect this; the `try/except` already handled it gracefully.

Bulk test orders end in `"delivered"` (a terminal, irreversible state) so they are noted in teardown as requiring no cleanup.

**Module docstring update**  
The module docstring was rewritten to list all test groups (original + extended) and to use the word "integration test" rather than "smoke test", which more accurately describes a suite that exercises state transitions, partial-failure paths, and data persistence.

**`CLAUDE.md` test command description**  
Updated to reference "all 49 MCP tools" and the four new test groups.

---

## 3 — MCP Skill Card Endpoint

### Problem

There was no machine-readable endpoint an AI agent could call to discover the MCP server's capabilities. Every new agent integration required either reading `MCP_GUIDE.md` manually or hardcoding knowledge of the tools. This created friction for:
- Bootstrapping new agents in automated pipelines.
- Auto-discovery in multi-server MCP setups.
- Building UI capability panels on top of the MCP server.

### Solution

**New file: `api/views/mcp_skill.py`**  

`MCPSkillView` is a DRF `APIView` with `permission_classes = [AllowAny]` that handles `GET /api/v1/mcp-skill/`.

`_build_skill_card(request)` assembles the full capability document at request time as a Python dict. The document structure:

```
schema_version, skill_id, display_name, version, description
api_base_url, skill_url
mcp_connection       ← three transports with start commands and env vars
authentication       ← tool + HTTP methods, note on agent account role
role_hierarchy       ← Admin / Manager / Staff / Any authenticated with capability lists
tools                ← 49 tools grouped by domain; each entry has name, role, description, params
resources            ← 12 URI entries
workflows            ← 5 prompt entries with steps
order_lifecycle      ← state list, transition table, key_rules
constraints          ← FK guards, immutability rules, validation rules
errors               ← 7 error codes with recovery actions
```

`_card_to_markdown(card)` renders the JSON structure into a formatted Markdown document (headings, tables, numbered steps) suitable for pasting directly into an AI system prompt.

`MCPSkillView.get()` inspects the `?format=` query param and `Accept` header to choose the format:
- `?format=markdown` or `Accept: text/markdown` → returns `text/markdown`
- Everything else → returns JSON via DRF's normal response stack

**Updated: `api/urls.py`**  
Added import of `MCPSkillView` and:
```python
path('mcp-skill/', MCPSkillView.as_view(), name='api-mcp-skill'),
```

### Design decisions

- **Public (no auth)** — The card describes what the server *can* do, not data from the database. An agent needs this information *before* it knows how to authenticate. No sensitive data is exposed.
- **Generated at request time** — There is no separate static file to keep in sync. If tools are added or the role matrix changes, `mcp_skill.py` must be updated, but drift is immediately visible.
- **Markdown format** — The Markdown output is designed to be pasted directly into a system prompt. It is self-contained: an AI reading only that document has everything it needs to interact with the MCP server correctly.

---

## 4 — MCP_GUIDE.md Corrections and Extensions

### Stale counts and tool name errors

| Location | Was | Now |
|---|---|---|
| §2 architecture diagram | `9 files` | `10 files` |
| §2 key design decisions | `all 43 tool closures` | `all 49 tool closures` |
| §3 directory — `inventory.py` | `# 3 tools` | `# 4 tools` |
| §3 directory — `orders.py` | `# 12 tools` | `# 15 tools` (listed all three bulk ops) |
| §3 directory | `settings.py` missing entirely | Added with `# 2 tools` |
| §4.4 tool module table | `inventory.py` row: 3, `orders.py` row: 12 | 4 and 15 respectively; added `settings.py` row |
| §5 Users tool catalog | `retailops_change_user_password` (wrong name) | `retailops_change_password` |
| §5 Users tool catalog | `new_password` only | Added `confirm_password` param |
| §5 Orders tool catalog | `retailops_create_order` missing `tax_amount?` | Added |
| §10 Claude Desktop step 5 | `all 43 retailops_* tools` | `all 49` |
| §14 LangChain comment | `Load all 43 RetailOps tools` | `Load all 49` |

### Wrong resource entry

The resource catalog in both §4.5 (table) and §6 (code block) listed `retailops://inventory/{id}` — this URI was never registered in `mcp_server/resources/handlers.py`. The actual 12th resource, `retailops://products/{id}/movements`, was missing from both locations.

**§4.5 table** and **§6 code block**: replaced `retailops://inventory/{id}` with `retailops://products/{id}/movements`.

### Error reference additions (§17)

Three MCP-layer guards introduced in Sessions 5–6 were missing from the error table:

| Guard | Message |
|---|---|
| Empty `order_ids` list | `order_ids must be a non-empty list of integers.` |
| Empty `adjustments` list | `adjustments must be a non-empty list.` |
| No field provided to settings update | `At least one of currency_code, currency_symbol, or decimal_places must be provided.` |

### New §19 — MCP Skill Card

Added a new section documenting the `GET /api/v1/mcp-skill/` endpoint:
- Endpoint URL and permission (public).
- Both formats (JSON and Markdown) with `curl` examples.
- Use-case table (bootstrapping agents, auto-discovery, capability verification, UI manifest).
- Maintenance note: the card is generated at request time from `api/views/mcp_skill.py`; no separate file to keep in sync.

Added §19 to the Table of Contents.

---

## 5 — API_GUIDE.md Corrections and Extensions

### Missing endpoints

Six endpoint families present in the codebase had no documentation in `API_GUIDE.md`.

**§4.1 Auth — password reset (2 endpoints)**  
`POST /api/v1/auth/password-reset/` and `POST /api/v1/auth/password-reset/confirm/` were registered in `api/urls.py` but absent from the guide. Added with full field tables, response formats, error cases, and a note about the `DecodedConsoleEmailBackend` development default.

**§4.8 Inventory — bulk adjustment**  
`POST /api/v1/inventory/bulk-adjust/` was added to the endpoint table and given a full subsection documenting the `adjustments` array format, field table, and the partial-success `{succeeded, failed}` response shape.

**§4.9 Orders — bulk transition**  
`POST /api/v1/orders/bulk-transition/` was added to the lifecycle table with three rows (confirm/ship/deliver). Added a full subsection documenting the `action` field (`confirm`, `ship`, `deliver`), the request body, and the partial-success response. Also added a **Submit guard** note to the lifecycle table: submitting an order with zero items returns `409 no_items` (the same guard that existed on `confirm` was extended to `submit` in Session 5 but was not documented).

**§4.11 Settings (new section)**  
`GET` and `PATCH /api/v1/settings/` documented with:
- Permission table.
- Full field table (`currency_code`, `currency_symbol`, `decimal_places`).
- Example request/response.
- Note that changing settings affects display only — stored decimal values are not converted.

**§4.12 MCP Skill Card (new section)**  
`GET /api/v1/mcp-skill/` documented with:
- Format table (JSON vs Markdown).
- JSON response shape overview.

### Missing and updated schema fields

**§4.4 Users — create/update fields table**  
Added `timezone` (IANA timezone string, default `UTC`) and `language` (BCP 47 code, default `en`) rows. These fields were added to the `User` model in Session 6 and are accepted by `POST /api/v1/users/` and `PATCH /api/v1/users/{id}/`.

**§5 User object schema**  
Added `timezone` and `language` fields to the example JSON. Added a sentence explaining that `RegionalMiddleware` activates both values per-request.

**§5 SystemSettings object schema (new)**  
Added the singleton `SystemSettings` object schema (returned by `GET /api/v1/settings/` and accepted by `PATCH`).

### Rate limiting corrections (§2.6)

The rate limiting section only listed the global tiers (20/min anonymous, 600/min authenticated). The codebase defines five additional per-endpoint throttle scopes that were undocumented:

| Scope | Limit | Applies to |
|---|---|---|
| `password_reset` | 5 / minute | `/auth/password-reset/` and `/confirm/` |
| `password_change` | 10 / minute | `/users/{id}/change-password/` |
| `order_transition` | 60 / minute | All six single-order transition actions + bulk-transition |
| `inventory_adjust` | 30 / minute | `/inventory/adjust/` and `/bulk-adjust/` |

Added a secondary table to §2.6 listing all four scopes.

### Table of Contents

Added entries for §4.11 Settings and §4.12 MCP Skill Card.

---

## Files Changed

| File | Type of change |
|---|---|
| `CLAUDE.md` | Updated — 6 sections backfilled from Sessions 5 and 6 |
| `test_mcp_tools.py` | Extended — 4 new test groups, updated teardown, updated docstring |
| `api/views/mcp_skill.py` | Created — `MCPSkillView`, `_build_skill_card()`, `_card_to_markdown()` |
| `api/urls.py` | Updated — import and URL registration for `MCPSkillView` |
| `MCP_GUIDE.md` | Updated — 13 corrections, 3 new error rows, new §19 |
| `API_GUIDE.md` | Updated — 6 new endpoint sections, schema updates, rate limit table, ToC |

---

## No Application Logic Changed

No models, migrations, views, serializers, URL patterns (other than the skill card URL), or MCP tool implementations were modified. All changes in this session are in:
- Project documentation (`CLAUDE.md`, `MCP_GUIDE.md`, `API_GUIDE.md`)
- The integration test suite (`test_mcp_tools.py`)
- A new read-only, public API view (`api/views/mcp_skill.py`)
