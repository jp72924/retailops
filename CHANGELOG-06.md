# CHANGELOG-06 — Regional Customization & Currency Bug Fix

**Session date:** 2026-04-11  
**Scope:** Per-user timezone and language settings, system-wide currency configuration, and a follow-on bug fix for the currency template filter.

---

## Overview

This session implemented full regional customization support for RetailOps. The system previously had no user-configurable locale settings: all datetimes were displayed in UTC, all currency amounts were rendered with a hardcoded `$` prefix across 19 template locations, and no translation infrastructure was active despite `USE_I18N = True` and `USE_TZ = True` already being set in Django settings.

The implementation introduced three distinct features:

- **Per-user timezone** — each authenticated user sees all dates and times converted to their own timezone on every request.
- **Per-user language** — each authenticated user's UI is served in their chosen language (English or Spanish; additional languages can be added via `.po` files).
- **System-wide currency** — a singleton `SystemSettings` model stores the currency symbol, ISO code, and decimal places; all monetary values across the application render using these settings.

A bug in the initial currency filter implementation was then identified and fixed: the filter failed silently and fell back to a hardcoded `$` whenever a `Decimal('0.00')` value was passed through Django's `|default` filter.

---

## Files Created

### `core/middleware.py`
New file. Defines `RegionalMiddleware`, which activates the authenticated user's configured timezone (using Python's built-in `zoneinfo.ZoneInfo`) and language (`django.utils.translation.activate`) at the start of every request, and deactivates timezone after the response to prevent bleed-across between requests.

```
MIDDLEWARE order: ... AuthenticationMiddleware → RegionalMiddleware → MessageMiddleware ...
```

Unauthenticated requests call `timezone.deactivate()` so UTC is used.

### `core/templatetags/__init__.py`
New empty file. Required by Django for `core/templatetags/` to be a valid Python package.

### `core/templatetags/regional.py`
New file. Defines the `|currency` template filter. Reads `SystemSettings.get()` on each call to obtain the configured symbol and decimal places, then formats any numeric input (Decimal, float, int, or numeric string) using Python's `f'{{:,.{places}f}}'` format spec.

**Initial version** (shipped with the feature):
```python
@register.filter
def currency(value):
    try:
        settings = SystemSettings.get()
        fmt = f'{{:,.{settings.decimal_places}f}}'
        return f'{settings.currency_symbol}{fmt.format(value)}'
    except Exception:
        return f'${value}'   # ← bug: hardcoded $ in fallback
```

**Fixed version** (after bug report — see Bug Fix section below):
```python
@register.filter
def currency(value):
    try:
        settings = SystemSettings.get()
    except Exception:
        settings = None
    symbol = settings.currency_symbol if settings else '$'
    places = settings.decimal_places if settings else 2
    try:
        numeric = Decimal(str(value)) if value not in (None, '') else Decimal('0')
        fmt = f'{{:,.{places}f}}'
        return f'{symbol}{fmt.format(numeric)}'
    except (InvalidOperation, ValueError):
        return f'{symbol}{value}'
```

### `core/templates/core/settings.html`
New template. Settings page available at `/settings/` to all authenticated users. Contains:
- **Regional Preferences** section (all users): timezone `<select>` populated from `zoneinfo.available_timezones()` (598 IANA zones), language dropdown (English / Spanish).
- **Currency Settings** section (Admin only): currency code (ISO 4217), symbol, and decimal places inputs with a live preview line showing the current format.

### `api/views/settings.py`
New file. Defines `SystemSettingsView` (a DRF `APIView`):
- `GET /api/v1/settings/` — any authenticated user; returns `{currency_code, currency_symbol, decimal_places}`.
- `PATCH /api/v1/settings/` — Manager or Admin only; partial update of currency fields.

---

## Files Modified

### `core/models.py`
**Added to `User` model:**
```python
timezone = models.CharField(max_length=64, default='UTC')
language = models.CharField(max_length=10, default='en')
```
Existing users receive `timezone='UTC'` and `language='en'` as migration defaults — no data loss.

**Added `SystemSettings` singleton model** at end of file:
```python
class SystemSettings(models.Model):
    currency_code   = models.CharField(max_length=3,  default='USD')
    currency_symbol = models.CharField(max_length=4,  default='$')
    decimal_places  = models.PositiveSmallIntegerField(default=2)

    def save(self, *args, **kwargs):
        self.pk = 1       # enforce singleton — only one row ever exists
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
```

### `core/admin.py`
- Imported `SystemSettings`.
- Added `Regional` fieldset to `UserAdmin` exposing the `timezone` and `language` fields.
- Registered `SystemSettingsAdmin` with `has_add_permission` guarded to one row and `has_delete_permission` returning `False`.

### `core/middleware.py` *(new, see above)*

### `core/views.py`
- Imported `SystemSettings` into the models import list.
- Updated `_order_form_context()` helper to call `SystemSettings.get()` and inject `currency_symbol` and `currency_decimals` into every order form context (used by the JS `fmtCurrency()` function in `order_detail.html`).
- Added `user_settings` view at the end of the file (GET + POST; accessible to all authenticated users; Admin branch also saves `SystemSettings` currency fields).

### `core/urls.py`
Added one URL pattern:
```python
path('settings/', views.user_settings, name='settings'),
```

### `core/migrations/0003_systemsettings_user_language_user_timezone.py`
Auto-generated migration (created via `python manage.py makemigrations core`). Creates the `SystemSettings` table and adds `language` and `timezone` columns to the `core_user` table with safe defaults.

### `core/templates/core/base.html`
Navbar changes:
- The link previously labelled **"Settings"** (visible only to Admins, pointing to `user-list`) was renamed to **"Users"** to accurately reflect its destination.
- A new **"Settings"** link was added pointing to the `settings` URL, visible to all authenticated users.

### `core/templates/core/dashboard.html`
- Added `{% load regional %}` after `{% extends %}`.
- Replaced `${{ stats.revenue_this_month|default:"0.00" }}` with `{{ stats.revenue_this_month|default:"0.00"|currency }}`.
- Replaced `${{ order.total_amount }}` (in recent orders table) with `{{ order.total_amount|currency }}`.

### `core/templates/core/order_list.html`
- Added `{% load regional %}`.
- Replaced `${{ order.total_amount }}` with `{{ order.total_amount|currency }}`.

### `core/templates/core/order_detail.html`
- Added `{% load regional %}`.
- Replaced all `${{ ... }}` monetary values with `{{ ...|currency }}` (unit price, line total, subtotal, tax, discount, grand total, payment amounts, outstanding balance — 9 replacements).
- Added JS constants at top of `extra_js` block:
  ```javascript
  const CURRENCY_SYMBOL  = "{{ currency_symbol|escapejs }}";
  const CURRENCY_DECIMALS = {{ currency_decimals }};
  function fmtCurrency(value) {
      return CURRENCY_SYMBOL + value.toFixed(CURRENCY_DECIMALS)
          .replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }
  ```
- Replaced all hardcoded `` `$${...}` `` string interpolations in `addLineItem()`, `recalcRow()`, and `recalcTotals()` with `fmtCurrency(...)` calls, so live recalculation as line items are added/changed also respects the configured currency.

### `core/templates/core/payment_list.html`
- Added `{% load regional %}`.
- Replaced `${{ payment.amount }}` with `{{ payment.amount|currency }}`.

### `core/templates/core/payment_detail.html`
- Added `{% load regional %}`.
- Replaced `${{ payment.amount }}`, `${{ payment.sales_order.total_amount }}`, `${{ payment.sales_order.amount_paid }}`, and `${{ payment.sales_order.amount_outstanding }}` with `|currency` equivalents (4 replacements).

### `core/templates/core/inventory_list.html`
- Added `{% load regional %}`.
- Replaced `${{ product.unit_price }}` with `{{ product.unit_price|currency }}`.

### `api/serializers/user.py`
- Added `'timezone'` and `'language'` to `UserReadSerializer.Meta.fields` (read-only).
- Added `'timezone'` and `'language'` to `UserWriteSerializer.Meta.fields` with `required: False` in `extra_kwargs`, so both fields are exposed and writable via the API.

### `api/urls.py`
- Imported `SystemSettingsView`.
- Added `path('settings/', SystemSettingsView.as_view(), name='api-settings')` to `urlpatterns`.

### `retailops/settings.py`
- Inserted `'core.middleware.RegionalMiddleware'` into `MIDDLEWARE` immediately after `AuthenticationMiddleware`.
- Added `LOCALE_PATHS = [BASE_DIR / 'locale']` (infrastructure for future `.po` translation files).

---

## Bug Fix — `|currency` Filter Hardcoded `$` Fallback

### Affected URLs
- `http://127.0.0.1:8000/orders/<id>/` — totals box (subtotal, tax, grand total spans)
- `http://127.0.0.1:8000/orders/new/` — same totals box with `order=None`

### Root Cause
Django's `|default` filter returns its argument if the piped value is **falsy**. `Decimal('0.00')` and `Decimal('0')` are falsy in Python (`bool(Decimal('0.00')) == False`). This means:

```
{{ order.subtotal|default:"0.00"|currency }}
```

When `order.subtotal` is `Decimal('0.00')` (or when `order` is `None` and the attribute resolves to `''`), `|default:"0.00"` returns the **string** `"0.00"`. The original `|currency` filter then called:

```python
'{:,.2f}'.format("0.00")   # → ValueError: Unknown format code 'f' for str
```

The broad `except Exception` clause caught this and returned `f'${value}'` — a hardcoded `$` regardless of the administrator's currency configuration.

Fields affected: tax amount (almost always `Decimal('0.00')`), subtotal and grand total on any new or zero-value order, and the discount span on read-only orders.

### Fix
`core/templatetags/regional.py` was updated to:

1. **Explicitly coerce the input to `Decimal`** before formatting, handling Decimal, float, int, numeric strings, `None`, and empty string uniformly via `Decimal(str(value))`.
2. **Read the currency symbol from `SystemSettings` in the fallback branch** too, so even if the numeric conversion fails, the configured symbol (not a hardcoded `$`) is used.

Verified with `python -c` test covering all edge cases after the fix:

| Input | Old output | New output (VES/Bs configured) |
|---|---|---|
| `Decimal('0.00')` | `$0.00` | `Bs0.00` |
| `"0.00"` (from `\|default`) | `$0.00` | `Bs0.00` |
| `Decimal('1234.50')` | `Bs1,234.50` | `Bs1,234.50` |
| `None` | `$None` | `Bs0.00` |
| `''` | `$` | `Bs0.00` |

---

## Database Changes

One new migration applied:

```
core/migrations/0003_systemsettings_user_language_user_timezone.py
  + Create model SystemSettings
  + Add field language to user (default='en')
  + Add field timezone to user (default='UTC')
```

No data is altered in existing rows. The `SystemSettings` row is created on first access via `get_or_create(pk=1)` with defaults `USD` / `$` / `2`.

---

## API Changes

| Method | Endpoint | Permission | Description |
|--------|----------|------------|-------------|
| GET | `/api/v1/settings/` | Any authenticated | Returns `{currency_code, currency_symbol, decimal_places}` |
| PATCH | `/api/v1/settings/` | Manager or Admin | Partial update of currency settings |

`GET /api/v1/users/<id>/` and list responses now include `timezone` and `language` fields.  
`POST /api/v1/users/` and `PATCH /api/v1/users/<id>/` now accept optional `timezone` and `language` fields.

---

## New HTML Route

| Method | URL | Permission | Description |
|--------|-----|------------|-------------|
| GET | `/settings/` | Any authenticated | Personal settings form (timezone, language) + currency section for Admins |
| POST | `/settings/` | Any authenticated | Save timezone/language; Admin also saves SystemSettings currency fields |

---

## Known Limitations / Future Work

| Area | Note |
|------|------|
| i18n strings | `{% load i18n %}` and `{% trans %}` tags are not yet applied to templates. The middleware activates the language per-request but no `.po` translation files exist yet. The `LOCALE_PATHS` setting and middleware are in place — adding translations requires running `makemessages`, editing `.po` files, and running `compilemessages`. |
| Timezone display in templates | Django's `|date:` filter renders in the active timezone automatically (because `USE_TZ=True` + middleware-activated timezone). No template changes were needed beyond the middleware. |
| Currency validation | `currency_code` accepts any 1–3 character string; no validation against the ISO 4217 list is performed. |
| MCP tools | ~~The `retailops_get_system_settings` MCP tool does not yet exist.~~ **Resolved** — see *Addendum* below. |

---

## Addendum — Settings MCP Tools

**Implemented in the same session**, directly after the regional customization work.

### Problem

The `GET /api/v1/settings/` and `PATCH /api/v1/settings/` endpoints existed (added in this session's main work) but had no MCP tool coverage. Agents needing to know the active currency symbol or decimal places — e.g. for reasoning about prices or formatting monetary outputs — had no structured tool to call; they would have had to use raw HTTP or rely on the response shape from other tools.

### New file — `mcp_server/tools/settings.py`

Defines `register_settings_tools(mcp, client)` containing two tools:

**`retailops_get_system_settings() -> dict`**
- Calls `GET /api/v1/settings/`
- Any authenticated role
- Returns `{currency_code, currency_symbol, decimal_places}`
- Useful context for agents reasoning about monetary values, formatting thresholds, or explaining price display to users

**`retailops_update_system_settings(currency_code?, currency_symbol?, decimal_places?) -> dict`**
- Calls `PATCH /api/v1/settings/` with only the provided fields
- Requires Manager or Admin role
- All three parameters are optional; raises `ValueError` if none are supplied (prevents a no-op PATCH)
- Returns the updated `SystemSettings` record
- Documents that stored decimal values are not converted — only display changes

### Changes — `mcp_server/server.py`

- Imported `register_settings_tools` from `.tools.settings`
- Added `register_settings_tools(mcp, client)` to the tool registration block
- Updated comment: `43 tools across 9 domains` → `49 tools across 10 domains`

### Changes — `CLAUDE.md`

- MCP tool count updated: 47 → 49
- Domain count updated: 9 → 10
- Settings domain and both tool names added to the MCP server description

### Changes — `MCP_GUIDE.md`

- Tool count updated: 47 → 49 (ASCII diagram + catalog header)
- New **Settings (2 tools)** section added to the tool catalog, between Dashboard and Customers

### Verification

```bash
# GET
curl http://127.0.0.1:8000/api/v1/settings/ -H "Authorization: Token <manager-token>"
→ {"currency_code": "USD", "currency_symbol": "$", "decimal_places": 2}

# PATCH
curl -X PATCH http://127.0.0.1:8000/api/v1/settings/ \
  -H "Authorization: Token <manager-token>" \
  -H "Content-Type: application/json" \
  -d '{"currency_code": "USD", "currency_symbol": "$", "decimal_places": 2}'
→ {"currency_code": "USD", "currency_symbol": "$", "decimal_places": 2}
```
