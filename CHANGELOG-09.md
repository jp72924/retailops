# CHANGELOG-09 — Kiosk Cart UX, Customer Filtering, and Dual-Currency Display

**Session date:** 2026-04-19
**Scope:** Five interconnected workstreams: (1) +/- quantity stepper buttons on the kiosk cart, (2) a dedicated remove button per cart item, (3) extending the admin customer list filter to include national ID and phone, (4) a back-office **secondary currency display** driven by a new `SystemSettings` field set with an admin-editable exchange rate, and (5) a mirrored but "reversed" dual-currency display in the kiosk PWA where the secondary currency (Bs.) is rendered prominently and the primary (USD) appears as a muted annotation.

---

## Overview

This session refined both the self-checkout cart ergonomics and the monetary display pipeline end-to-end. The back-office gains a fully-optional secondary currency that, when enabled, appears next to every primary-currency amount in smaller muted text — without touching any of the 18+ existing `|currency` usage sites. The kiosk, which has its own independent live-rate pipeline, receives a display-only change that places the local currency (Bs.) as the dominant text with the USD equivalent as the secondary annotation — i.e. the visual relationship is inverted versus the back-office because the kiosk user's frame of reference is the local currency.

All changes preserve existing behavior when features are disabled: the back-office `|currency` filter still emits plain text when the secondary currency is disabled, and the kiosk formatter functions `formatUsd` / `formatBs` remain available for any code paths that opted not to adopt `formatDual`.

---

## 1 — Kiosk Cart Quantity Stepper

### 1.1 ScanScreen cart rendering

**`kiosk/app/components/ScanScreen.js`** — the `_renderCart()` helper's per-item template was extended from a single "price" line to a three-section row:

```
[ emoji ]  [ name / SKU + unit price ]  [ − qty + ]  [ line total ]  [ ✕ ]
```

New DOM elements per cart row:

- `.qty-stepper` — flex container wrapping the minus button, the current quantity, and the plus button
- `.qty-btn.qty-dec` — decrement button with `data-id` attribute pointing at the cart item's product ID
- `.qty-value` — span displaying the current quantity
- `.qty-btn.qty-inc` — increment button with the same `data-id`

### 1.2 Increment / decrement handlers

Two new async methods on the `ScanScreen` object:

- **`_incrementQty(id, container)`** — reads `store.get('cart')`, finds the matching line item, then calls `getProduct(id)` to re-validate current stock. If the new qty would exceed `current_stock`, shows an inline toast via `showToast()` and bails without mutating state. Otherwise mutates the cart array in place, calls `store.set('cart', …)`, and re-renders via `this._refreshCart(container)`. The async call is awaited so the render can't race with a second click during the API round-trip.

- **`_decrementQty(id, container)`** — synchronous; decrements qty if > 1, or removes the line item entirely if qty === 1. Re-renders via `_refreshCart`.

### 1.3 Event delegation in `_wireCart`

The existing `_wireCart(container)` was extended with a single delegated click listener on `#cart-list` that routes by `e.target.closest('.qty-inc' | '.qty-dec' | '.cart-item-remove')`. Using `closest()` matters because the user may tap the inner text of the button on mobile, not the button itself.

### 1.4 Styles (`kiosk/styles.css`)

```css
.qty-stepper { display: flex; align-items: center; gap: 8px; ... }
.qty-btn     { width: 32px; height: 32px; border-radius: 50%; ... }
.qty-value   { min-width: 24px; text-align: center; font-weight: 600; }
```

### 1.5 ES module cache workaround

During verification the browser persistently served a cached copy of `ScanScreen.js` despite the file being correct on disk. A side-by-side comparison using `fetch('/app/components/ScanScreen.js?bust=' + Date.now())` confirmed the server was returning the latest version while the DOM still rendered the old version. The workaround was to dynamically re-import the module with a cache-busting query param and re-mount:

```js
import('/app/components/ScanScreen.js?v=' + Date.now())
  .then(m => m.ScanScreen.mount(document.getElementById('app')));
```

This is a development-only note — production PWA deployments rely on the service worker's standard version-based cache invalidation.

---

## 2 — Kiosk Remove-Item Button

### 2.1 Template addition

**`kiosk/app/components/ScanScreen.js`** — the cart row template gained a fourth button next to the stepper:

```html
<button class="cart-item-remove" data-id="${item.id}" aria-label="Eliminar producto">✕</button>
```

### 2.2 Handler via existing event delegation

The `_wireCart` listener's branch for `.cart-item-remove` filters the product out of the cart and re-renders:

```js
if (remBtn) {
  const id = Number(remBtn.dataset.id);
  store.set('cart', (store.get('cart') ?? []).filter(i => i.id !== id));
  this._refreshCart(container);
}
```

No separate async call — removal is local state only. The product is only deducted from inventory at checkout time via `atomicCheckout`, so removing a line item before checkout has no API implications.

### 2.3 Style

`.cart-item-remove` — minimal icon button with a hover state that tints the ✕ red, visually distinguishing it from the neutral stepper buttons.

---

## 3 — Customer List National ID & Phone Filter

### 3.1 View filter extension

**`core/views.py` — `customer_list`:** the existing search `Q` expression gained two OR clauses:

```python
qs = qs.filter(
    Q(first_name__icontains=query) |
    Q(last_name__icontains=query)  |
    Q(email__icontains=query)      |
    Q(national_id__icontains=query)|
    Q(phone__icontains=query)
)
```

`icontains` preserves the existing case-insensitive, partial-match behavior for the two new fields, so typing the leading three digits of a phone or the first few digits of a national ID now narrows the list.

### 3.2 Template placeholder

**`core/templates/core/customer_list.html`** — the search input placeholder was updated from "Search name or email…" to "Search name, email, ID or phone…" so users discover the expanded filter.

No URL, pagination, or API changes — the REST API already exposed these fields through the existing customer filter.

---

## 4 — Back-Office Secondary Currency Display

### 4.1 Motivation

RetailOps previously displayed every monetary amount in a single configurable primary currency (`SystemSettings.currency_code/symbol/decimal_places`). In economies where prices are quoted in one currency but paid/thought-of in another (USD list prices, bolívares at the counter), staff want a secondary currency shown subtly alongside the primary one, with an admin-settable exchange rate. The kiosk was explicitly left alone — it has its own live-rate pipeline and was treated in workstream §5.

### 4.2 Model additions — `core/models.py`

Added five fields to `SystemSettings` and a `clean()` validator:

```python
secondary_currency_enabled = models.BooleanField(default=False)
secondary_currency_code    = models.CharField(max_length=3, blank=True, default='')
secondary_currency_symbol  = models.CharField(max_length=4, blank=True, default='')
secondary_decimal_places   = models.PositiveSmallIntegerField(default=2)
secondary_exchange_rate    = models.DecimalField(
    max_digits=20, decimal_places=8, default=Decimal('1')
)

def clean(self):
    if self.secondary_currency_enabled:
        if not self.secondary_currency_symbol.strip():
            raise ValidationError({'secondary_currency_symbol':
                'Required when secondary currency is enabled.'})
        if self.secondary_exchange_rate is None or self.secondary_exchange_rate <= 0:
            raise ValidationError({'secondary_exchange_rate':
                'Must be greater than zero.'})
```

All fields have defaults so the migration applied cleanly with no backfill decision required.

**Migration:** `core/migrations/0006_systemsettings_secondary_currency_code_and_more.py` — autogenerated via `makemigrations`, applied without issue.

### 4.3 Template filter upgrade — `core/templatetags/regional.py`

The `|currency` filter was rewritten:

- When `secondary_currency_enabled` is false: returns the same plain string as before (`${amount}`) — backward compatible with every existing usage, including JS recalc target cells that expect `textContent` to be parseable as a number.
- When enabled: returns an HTML fragment built with `format_html()`:
  ```python
  return format_html(
      '{}{}<span class="currency-secondary"> \u2248 {}{}</span>',
      symbol, primary_str, settings.secondary_currency_symbol, sec_str,
  )
  ```

`format_html()` (rather than `mark_safe`) is the critical XSS defense — symbol fields are admin-editable and could contain `<script>`. `format_html` escapes every placeholder individually.

Two new filters:

- **`|currency_plain`** — primary-only plain text, for CSV/email or any context where HTML is unsafe or undesired. Used in the order form's live-recalc cells so the JS can freely overwrite with `textContent`.
- **`|currency_secondary`** — returns just the " ≈ Sym X.XX" annotation (or empty string if disabled), for templates that need the primary and secondary values in separate DOM nodes.

A private `_format_amount(value, places)` helper centralizes `{:,.Nf}` numeric formatting so the two paths don't drift.

Decimal precision: secondary amount is computed via `Decimal.quantize(Decimal(10) ** -sec_places, rounding=ROUND_HALF_UP)` to avoid float error.

### 4.4 CSS — `core/templates/core/base.html`

```css
.currency-secondary {
  font-size: 0.82em; color: var(--muted); font-weight: 400;
  margin-left: 2px; white-space: nowrap;
}
```

The `em` unit means the secondary text auto-scales wherever the primary amount is rendered — works equally well inline in a table cell (14 px → 11.5 px) or next to a 28 px dashboard stat (28 px → 23 px).

### 4.5 Settings form — `core/templates/core/settings.html`

A toggle-gated block was added inside the Admin-only currency card:

- Checkbox: `secondary_currency_enabled`
- 4-column grid: code, symbol, decimal places (0–4), exchange rate (`type=number`, `step=0.00000001`)
- Preview line showing `{primary_symbol}1.00 ≈ {secondary_symbol}{rate}`
- Per-field invalid-feedback driven by the `errors` dict passed from the view

### 4.6 Save logic — `core/views.py` → `user_settings`

After the existing primary-currency block, added secondary-currency parsing + `full_clean()` validation. On `ValidationError`, re-renders the settings page with `e.message_dict` unpacked to a `{field: first_message}` dict so the template's per-field error slots can display the first error for each field.

### 4.7 Order form JS recalc — `core/templates/core/order_detail.html`

The JS recalc flow was extended to maintain dual-currency display as the user edits line items:

1. Each cell that previously held `{{ value|currency }}` was split into two adjacent spans:
   ```html
   <strong id="line-total-1">{{ item.line_total|currency_plain }}</strong>
   <span class="currency-secondary" id="line-total-1-sec">{{ item.line_total|currency_secondary }}</span>
   ```
2. Four new JS constants: `SECONDARY_ENABLED`, `SECONDARY_SYMBOL`, `SECONDARY_DECIMALS`, `SECONDARY_RATE` — all sourced from context and `|escapejs`-escaped.
3. `fmtSecondary(value)` parallel to the existing `fmtCurrency(value)`.
4. `setCurrencyCell(primaryId, value)` helper replaces the three previous direct `textContent` assignments — writes the primary span and the `-sec` sibling.
5. `recalcRow` and `recalcTotals` now call `setCurrencyCell(...)` for each of the line total, subtotal, and grand total.

Critically, **no `innerHTML` is used** in the JS path. Both spans are written with `textContent`, so even if `SECONDARY_SYMBOL` escapes the JS string boundary it cannot become executable HTML.

### 4.8 `_order_form_context` — `core/views.py`

Extended with four extra keys: `secondary_currency_enabled`, `secondary_currency_symbol`, `secondary_decimal_places`, `secondary_exchange_rate`. Consumed by the JS constants in §4.7.

### 4.9 REST API — `api/views/settings.py`

`SystemSettingsSerializer.Meta.fields` gained the five new field names. Two custom validators were added:

- `validate_secondary_exchange_rate(value)` — rejects `value <= 0` with `Must be greater than zero.`
- `validate(attrs)` — enforces the "symbol required when enabled" invariant by merging `attrs` over the current instance state, so a PATCH that enables the secondary currency without also supplying a symbol is rejected even though the serializer wouldn't normally see the existing empty symbol.

Permissions unchanged — PATCH still requires Manager+.

### 4.10 MCP tool — `mcp_server/tools/settings.py`

`retailops_update_system_settings` gained five optional kwargs:

```python
secondary_currency_enabled: Optional[bool] = None,
secondary_currency_code: Optional[str] = None,
secondary_currency_symbol: Optional[str] = None,
secondary_decimal_places: Optional[int] = None,
secondary_exchange_rate: Optional[str] = None,  # string to preserve Decimal precision
```

The rate is validated client-side as a positive `Decimal` before being converted to string and sent. The docstring explicitly notes the kiosk has its own live-rate pipeline unaffected by this setting.

### 4.11 Verification

1. Migration applied cleanly (`makemigrations` + `migrate`).
2. Enabled secondary currency on `/settings/` with `VES` / `Bs.` / 2 decimals / rate `36.5`. The 9-field `GET /api/v1/settings/` response reflected all five new fields.
3. Dashboard revenue stat and order-list totals rendered as `$X.XX ≈ Bs.Y,YYY.YY` with the secondary part visibly smaller and muted.
4. Order detail live recalc: changing line 1 quantity from 2 → 5 updated both the primary and secondary cells with no flash of raw HTML between states.
5. Disabling the toggle reverted every page to plain `$X.XX` (backward compatible).
6. `PATCH /api/v1/settings/` with `secondary_exchange_rate: "0"` → 400 with `"Must be greater than zero."`
7. XSS sanity: `PATCH` with `secondary_currency_symbol: "<b>X"` → served as `&lt;b&gt;X` in the page HTML, not as executable `<b>` markup.

---

## 5 — Kiosk Reversed Dual-Currency Display

### 5.1 Design inversion

The kiosk is Venezuela-facing: products are stored with `unit_price` in USD, but shoppers think and pay in bolívares. The existing kiosk already had its own live-rate pipeline (`currency.js → loadLiveRate()`, 4-hour sessionStorage cache with stale-while-revalidate) and rendered prices in Bs. only (except one hardcoded USD subtotal line). The user requested "reverse" the relationship from the back-office: show both currencies everywhere at least one currently appears, but with the secondary (Bs.) prominent and the primary (USD) as the smaller annotation.

### 5.2 New formatter — `kiosk/app/currency.js`

Three exports added:

- **`formatUsdSymbol(usd)`** — `$X.XX` with 2 decimals and `\B(?=(\d{3})+(?!\d))` thousands separators. Kept separate from the Intl locale path because USD pricing is always the source of truth in this kiosk and is rendered with `$` regardless of the shopper's locale.

- **`_escHtml(s)`** — private helper: `&`, `<`, `>`, `"`, `'` escape. Used on both the Bs. and USD formatted strings before they are embedded in the returned HTML so an admin-editable symbol from RetailOps settings cannot inject markup.

- **`formatDual(usd)`** — the public formatter that returns an XSS-safe HTML fragment:
  ```
  Bs. 127,39<span class="price-secondary"> ≈ $3.49</span>
  ```
  The leading Bs. text node inherits the container's (prominent) styling; the `.price-secondary` span overrides font size, weight, and color for the muted annotation.

The live-rate pipeline (`initCurrency`, `applyDisplaySettings`, `loadLiveRate`, `_fetchAndCacheRate`, `_applyRate`) is unchanged. Existing `formatBs`/`formatUsd` exports are preserved for any caller that prefers the single-currency form.

### 5.3 CSS — `kiosk/styles.css`

```css
.price-secondary {
  font-size: 0.72em; font-weight: 400; color: var(--ft-gray-text);
  margin-left: 4px; white-space: nowrap; letter-spacing: 0;
}
```

Placed adjacent to the existing `.search-result-price` definition for locality.

### 5.4 Call-site updates

All four components that render monetary amounts were updated. For each, the import line gained `formatDual` and the render path switched from single-currency to dual. Where `esc(formatBs(...))` was previously used to defensively HTML-escape the plain-text output, the `esc()` wrapper was removed because `formatDual` intentionally returns HTML (already internally escaped via `_escHtml`).

| File | Line | Before | After |
|---|---|---|---|
| `ScanScreen.js` | 336 | `formatUsd(p.unit_price)` | `formatDual(p.unit_price)` |
| `ScanScreen.js` | 552 | `formatUsd(item.unit_price)` | `formatDual(item.unit_price)` |
| `ScanScreen.js` | 559 | `formatUsd(Number(item.unit_price) * item.qty)` | `formatDual(Number(item.unit_price) * item.qty)` |
| `ScanScreen.js` | 570 | Hardcoded `$${total.toFixed(2)}`, label "Subtotal (USD)" | `formatDual(total)`, label "Subtotal" |
| `ScanScreen.js` | 573 | `formatBs(usdToBs(total))` | `formatDual(total)` |
| `PaymentScreen.js` | 71 | `esc(formatBs(totalBs))` | `formatDual(total)` |
| `ProcessingScreen.js` | 46 | `esc(formatBs(totalBs))` | `formatDual(totalUsd ?? 0)` |
| `SuccessScreen.js` | 38 | `formatBs(usdToBs(Number(item.unit_price) * item.qty))` | `formatDual(Number(item.unit_price) * item.qty)` |
| `SuccessScreen.js` | 69 | `esc(formatBs(totalBs))` | `formatDual(totalUsd)` |

### 5.5 Subtotal row label

The dedicated USD subtotal row on ScanScreen (`"Subtotal (USD)" $X.XX`) was the only place the kiosk previously showed USD alone. Per the explicit requirement that "both currencies must always be displayed wherever at least one currently appears", the row now shows dual currency. The `(USD)` qualifier was dropped because it's no longer a USD-only row. The subtotal and total rows currently show identical values (no discounts are applied in the kiosk flow); they remain separate rows for future extensibility.

### 5.6 Verification

Tested in the kiosk preview at `http://127.0.0.1:3000/`:

- **Formatter unit check:** `formatDual(3.49)` → `Bs. 127,39<span class="price-secondary"> ≈ $3.49</span>`; `formatDual(1234.56)` → `Bs. 45.061,44<span class="price-secondary"> ≈ $1,234.56</span>` (locale grouping with `.` thousands and `,` decimal for `es-VE`, matching the existing `Intl.NumberFormat` path).
- **Live render:** after driving the cedula flow and searching "notebook", the search result card rendered `Bs. 174,50 ≈ $3.49` with the secondary portion visibly muted.
- **Computed styles in the rendered DOM:**
  - `.search-result-price`: 13 px, 700, `rgb(0, 87, 168)` — prominent (Ferreteria blue)
  - `.price-secondary`: 9.36 px (0.72 × 13), 400, `rgb(107, 114, 128)` — muted gray

All four surfaces (search result, cart item detail, cart item price, cart summary with subtotal + total) render the dual format consistently. The live exchange rate pipeline, 4-hour cache, and stale-while-revalidate logic are untouched — `formatDual` is a pure display-layer change that reuses `usdToBs` for the conversion.

---

## Files Modified

### Back-office (workstreams §3 + §4)

- `core/models.py` — added `ValidationError` import; 5 new `SystemSettings` fields; `clean()` validator
- `core/migrations/0006_systemsettings_secondary_currency_code_and_more.py` — autogenerated
- `core/templatetags/regional.py` — rewritten `|currency`; new `|currency_plain`, `|currency_secondary`; factored `_format_amount`, `_safe_settings`
- `core/templates/core/base.html` — `.currency-secondary` CSS rule
- `core/templates/core/settings.html` — toggle-gated 4-field block + preview line
- `core/views.py` — extended `customer_list` filter; extended `user_settings` save logic with `full_clean()`; extended `_order_form_context`
- `core/templates/core/customer_list.html` — updated placeholder text
- `core/templates/core/order_detail.html` — dual spans for line/subtotal/grand total cells; new JS constants and `setCurrencyCell` helper
- `api/views/settings.py` — extended serializer fields + two custom validators
- `mcp_server/tools/settings.py` — 5 new optional kwargs; Decimal string validation

### Kiosk (workstreams §1, §2, §5)

- `kiosk/app/currency.js` — new `formatUsdSymbol`, `_escHtml`, `formatDual`
- `kiosk/styles.css` — `.qty-stepper`, `.qty-btn`, `.qty-value`, `.cart-item-remove`, `.price-secondary`
- `kiosk/app/components/ScanScreen.js` — cart template with stepper + remove button; `_incrementQty`, `_decrementQty`; event delegation in `_wireCart`; `formatDual` call-sites
- `kiosk/app/components/PaymentScreen.js` — `formatDual` import + total-display call-site
- `kiosk/app/components/ProcessingScreen.js` — `formatDual` import + `proc-total` call-site
- `kiosk/app/components/SuccessScreen.js` — `formatDual` import + receipt line items and total call-sites

---

## Architectural Notes

**Kiosk / back-office decoupling preserved.** The kiosk's currency configuration (`CONFIG.CURRENCY_SYMBOL`, `CONFIG.USD_TO_BS_RATE`, and the `EXCHANGE_RATE_API_URL` live-rate pipeline) is fully independent of the new `SystemSettings.secondary_*` fields. An admin changing the back-office secondary currency has zero effect on the kiosk, and conversely a change to the kiosk's live rate does not propagate to back-office pages. This is intentional — the back-office rate is a static admin-set reference; the kiosk rate must be live for point-of-sale accuracy.

**XSS defense at two layers.** The back-office symbol fields pass through `format_html` in the template filter (Django's per-placeholder escape). The kiosk passes its output through `_escHtml` before the `<span>` wrapper and relies on each consumer to use `innerHTML` only on formatter output it trusts. Neither path ever concatenates raw user input into HTML without escaping.

**Filter upgrade backward-compatible.** Because `|currency` still returns plain text when `secondary_currency_enabled=false`, no existing template or JS consumer needed to change. The new `|currency_plain` exists specifically for the order-detail JS recalc path, where the dual format would be double-rendered (once by the filter, once by the adjacent sibling span).

**Kiosk cart quantity validation is async.** Because the increment handler calls `getProduct(id)` to re-check stock, a fast double-tap could race. The implementation awaits each API call before re-rendering, and the renderer is idempotent — no duplicate items ever appear because it works off the single source of truth in `store.get('cart')`.

---

## Verification Summary

| # | Scenario | Result |
|---|----------|--------|
| 1 | `makemigrations` + `migrate` after model changes | Applied cleanly, no backfill required |
| 2 | Customer list search by partial national ID / phone | Both fields surfaced matching rows |
| 3 | `/settings/` enable toggle + save with VES, Bs., 36.5 | Persisted; `GET /api/v1/settings/` returned all 5 new fields |
| 4 | Dashboard / orders / order detail — secondary visible | Rendered `≈ Bs.X,XXX.XX` in smaller muted text |
| 5 | Order line-item qty change triggers JS recalc | Both primary and secondary cells updated via `textContent`; no raw HTML flash |
| 6 | Disable toggle, reload pages | Plain primary-only text (backward compatible) |
| 7 | `PATCH` `secondary_exchange_rate: "0"` | 400 with `"Must be greater than zero."` |
| 8 | `PATCH` `secondary_currency_symbol: "<b>X"` | Rendered as `&lt;b&gt;X` (escaped) |
| 9 | Kiosk `formatDual(3.49)` in browser | `Bs. 127,39<span class="price-secondary"> ≈ $3.49</span>` |
| 10 | Kiosk search result live in DOM | Dominant Bs. 13px/700/blue; muted $ 9.36px/400/gray |
| 11 | Kiosk cart stepper +/−, remove | All three buttons wired via event delegation |
