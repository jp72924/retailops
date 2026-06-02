## RetailOps Kiosk — Integration Architecture Analysis

---

### Part 1: Dependency Map

The Kiosk PWA is a **browser-side client** with zero server-side components of its own. Every capability it has is borrowed from RetailOps through five dependency layers:

#### Layer 1 — Transport (CORS)
`KioskCORSMiddleware` in `core/middleware.py` is the only structural change the main system makes to support the kiosk. It intercepts all `OPTIONS` preflight requests and injects `Access-Control-Allow-*` headers for paths under `/api/v1/`. The allowed origins are read from `KIOSK_CORS_ORIGINS` (env var). In `DEBUG` mode, `localhost`, `127.0.0.1`, and `::1` are automatically allowed regardless of that variable.

Without this middleware, a browser-based kiosk cannot make any API call at all — it is a hard prerequisite.

#### Layer 2 — Authentication
The kiosk uses a single shared service account. At startup it calls `POST /api/v1/auth/token/` with an email and password read from `window.__KIOSK_CONFIG__` (injected directly into `index.html`). The returned token is held in `sessionStorage['kiosk_token']` for the lifetime of the browser session and attached as `Authorization: Token <token>` on every subsequent request.

The account must hold **Manager** role, which is required for order confirmation and stock deduction. There is no kiosk-specific role — it uses the same `IsManagerOrAdmin` permission class as a human manager.

#### Layer 3 — API Endpoints (15 endpoints, in dependency order)

| Step | Method + URL | Purpose |
|---|---|---|
| 1 | `POST /api/v1/auth/token/` | Obtain token at startup |
| 2 | `GET /api/v1/settings/` | Load currency symbol/decimals |
| 3 | `GET /api/v1/customers/?search=<derived-email>` | Cédula lookup |
| 4 | `POST /api/v1/customers/` | Register new customer |
| 5 | `GET /api/v1/products/?search=<q>&is_active=true&page_size=6` | Product catalog search |
| 6 | `GET /api/v1/products/{id}/` | Per-item stock re-validation at checkout |
| 7 | `POST /api/v1/orders/` | Create draft order |
| 8 | `POST /api/v1/orders/{id}/submit/` | Draft → Pending |
| 9 | `POST /api/v1/orders/{id}/confirm/` | Pending → Confirmed (deducts stock) |
| 10 | `POST /api/v1/payments/` | Record payment (auto-transitions to Paid) |
| 11 | `POST /api/v1/orders/{id}/ship/` | Paid → Shipped |
| 12 | `POST /api/v1/orders/{id}/deliver/` | Shipped → Delivered |
| 13 | `DELETE /api/v1/orders/{id}/` | Abandon a Draft on cancel/timeout |
| 14 | `POST /api/v1/orders/{id}/cancel/` | Restore stock on a Confirmed order |
| 15 | `POST /api/v1/auth/token/revoke/` | Destroy token on clean shutdown |

Steps 7–12 must execute in strict order. Steps 13–14 are exception paths. A single checkout therefore requires **a minimum of 10 sequential network round trips** in the happy path (steps 1–2 at startup, 3, 5, 7, 8, 9, 10, 11, 12).

#### Layer 4 — Data Models
The kiosk reads or writes to **8 of the 10 core models**:

| Model | Access |
|---|---|
| `User` | Reads (role check); writes indirectly (recorded_by, created_by) |
| `Customer` | Read + Write (lookup and creation) |
| `Product` | Read (catalog, stock status) |
| `SalesOrder` | Read + Write (full lifecycle) |
| `SalesOrderItem` | Write (created with order) |
| `Payment` | Write (records payment) |
| `InventoryMovement` | Write (triggered by confirm) |
| `SystemSettings` | Read (currency config) |

Only `Role` and `SequenceCounter` are untouched directly. `SequenceCounter` is used internally by `SalesOrder.save()` and `Payment.save()`.

#### Layer 5 — Client-Side Configuration
All deployment-specific values are injected as a JavaScript literal in `index.html`:

```javascript
window.__KIOSK_CONFIG__ = {
  BASE_URL, API_PATH,
  KIOSK_USER_EMAIL, KIOSK_PASSWORD,   // ← service account credentials
  STORE_ID, STORE_NAME, STORE_ADDRESS,
  KIOSK_STATION_NUMBER,
  USD_TO_BS_RATE,                      // ← hardcoded exchange rate
  EXCHANGE_RATE_API_URL,
  LOCALE, CURRENCY_CODE, CURRENCY_SYMBOL, DECIMAL_PLACES,
  LOW_STOCK_THRESHOLD,
  ENABLED_PAYMENT_METHODS,
  IDLE_TIMEOUT_SECONDS,
  PROCESSING_TIMEOUT_SECONDS,
  APP_VERSION
};
```

This is the only mechanism for multi-store configuration — you deploy a different `index.html` per station.

---

### Part 2: Security Risks

#### Risk 1 — Critical: Service Account Password Exposed in Static HTML

**What:** `KIOSK_USER_EMAIL` and `KIOSK_PASSWORD` are embedded as plaintext literals in `index.html` and served as a static file. Any user who opens DevTools → Sources, or does `curl http://kiosk/index.html`, reads the credentials immediately.

**Impact:** Those credentials grant **Manager-level access to the entire RetailOps API** — not just kiosk operations. An attacker can:
- Cancel any order in the system
- Adjust inventory arbitrarily (bulk-adjust endpoint)
- Browse the full customer list (names, addresses, Cédulas derived from emails)
- Read all orders and payment history
- Confirm, ship, and deliver any pending order

There is no scoping mechanism. The kiosk account is a full Manager.

**Root cause:** The spec acknowledges "serve over HTTPS only" as a mitigation, but that only prevents network interception — it does nothing against local inspection of a running kiosk, a stolen kiosk device, or a disgruntled employee.

---

#### Risk 2 — Critical: Single Shared Account Across All Stations

**What:** Every kiosk station — regardless of store or location — authenticates with the same `kiosk@retailops.local` account and the same password.

**Impact (Blast Radius):**
- One compromised station compromises all stations globally.
- Revoking the token to respond to an incident (`POST /api/v1/auth/token/revoke/`) immediately breaks every running kiosk simultaneously with no way to isolate the affected unit.
- All orders created by all stations are attributed to the same `created_by` user — the audit trail cannot distinguish which physical terminal placed which order without relying on the free-text `notes` field (e.g. `"Kiosk order — Estación 3"`).
- The 600 req/min global throttle is shared across all stations. A busy station can degrade service for others.

---

#### Risk 3 — High: Principle of Least Privilege Violated

**What:** The kiosk service account holds Manager role. The operations the kiosk legitimately needs are a subset of what Staff provides, plus the single ability to confirm orders. Manager role grants much more.

**What the kiosk actually needs vs. what it gets:**

| Capability | Needs? | Gets? |
|---|---|---|
| Create/submit orders | Yes | Yes (Staff) |
| Confirm orders (deduct stock) | Yes | Yes (Manager) |
| Ship/deliver orders | Yes | Yes (Staff) |
| Record payments | Yes | Yes (Staff) |
| Create customers | Yes | Yes (Staff) |
| Read products | Yes | Yes (Staff) |
| Cancel *any* order in the system | No | Yes (Manager) |
| Manually adjust inventory | No | Yes (Manager) |
| Access bulk-transition endpoint | No | Yes (Manager) |
| Access bulk-adjust inventory | No | Yes (Manager) |
| PATCH system settings (currency) | No | No (Manager+ ✓) |

A compromised kiosk token can cancel any confirmed order anywhere in the system and bulk-adjust inventory to zero.

---

#### Risk 4 — High: DRF Tokens Never Expire

**What:** Django REST Framework's `TokenAuthentication` issues tokens with no expiry. The token the kiosk obtains at startup is valid until explicitly revoked via `POST /api/v1/auth/token/revoke/`. The kiosk only calls this endpoint on a clean, intentional shutdown.

**Impact:** 
- A token leaked via sessionStorage (XSS), device theft, or log exposure remains valid indefinitely.
- The idle timeout (60 seconds) only clears `sessionStorage` in the browser — it does not revoke the server-side token. The token lives on the server until `revoke` is explicitly called.
- There is no background token rotation or expiry mechanism in the codebase.

---

#### Risk 5 — High: Customer PII Stored Without Protection

**What:** The kiosk derives a customer's email from their Venezuelan Cédula using the deterministic formula `{prefix}-{number}@kiosk.retailops.local`, then stores the raw Cédula in the Customer's `notes` field (e.g. `"V-12.345.678"`).

**Impact:**
- The `notes` field is a free-text field visible to all Staff, Manager, and Admin users in both the HTML UI and REST API. There is no access control on it beyond the role gate for the customer list.
- The derivation formula is documented and deterministic — an attacker with the formula can enumerate all registered kiosk customers by iterating Cédula numbers (`GET /api/v1/customers/?search=v-00000001@kiosk.retailops.local`, etc.).
- `email` is a unique field — if a customer already has a non-kiosk `Customer` record with a real email, registration fails with a 400 and the error response leaks that the email is taken (user enumeration).

---

#### Risk 6 — Medium: No Server-Side Stock Guard at Confirm Time

**What:** The kiosk performs a client-side stock re-validation before checkout: it re-fetches each `GET /api/v1/products/{id}/` and blocks if `is_out_of_stock`. However, `current_stock` is a **computed property** (`@property` in `models.py` that aggregates `InventoryMovement`). The `confirm` endpoint deducts stock by creating `InventoryMovement` records atomically.

**The race:** Two kiosk stations simultaneously complete the pre-checkout guard (both see stock = 1), both proceed to confirm. The first confirm deducts stock to 0. The second confirm also succeeds — the `confirm` view does not check whether the deduction would push stock negative. Stock ends up at -1, and two customers receive the same item.

This is a classic TOCTOU (time-of-check/time-of-use) bug. The fix requires a guard at the point of deduction in the `confirm` view, not at the client.

---

#### Risk 7 — Medium: No HTTPS Enforcement in CORS Middleware

**What:** `KioskCORSMiddleware` reads `KIOSK_CORS_ORIGINS` and allows those origins. The middleware does not enforce that origins must use `https://`. In `DEBUG=True` mode, it explicitly adds `http://localhost` and `http://127.0.0.1`.

**Impact:** If `DEBUG` is accidentally left on in a production deployment (which is a noted risk in the Known Gaps table), CORS is effectively unrestricted — all localhost origins are allowed, and the local network can make credentialed cross-origin requests. The service account password in `index.html` becomes a network-layer vulnerability, not just a device-layer one.

---

#### Risk 8 — Low: Client-Generated Payment Reference Numbers

**What:** The 8-character alphanumeric `reference_number` field on `Payment` is generated by the kiosk client in JavaScript before the `POST /api/v1/payments/` call.

**Impact:** A tampered or buggy kiosk could submit duplicate reference numbers, fabricated reference numbers, or structured values that look like legitimate bank transfer IDs. The API does not validate uniqueness of `reference_number` per-payment or per-order — two payments with identical reference numbers can coexist. This weakens the payment audit trail.

---

### Part 3: Architectural Inefficiencies

#### Inefficiency 1 — 10 Sequential API Calls Per Checkout

The happy-path checkout requires: auth → settings → customer lookup → product search → stock guard (N calls) → order create → submit → confirm → payment → ship → deliver. Every step is a sequential network round trip — no batching, no pipelining.

**Consequence:** On a slow in-store network (a very common retail scenario), each round trip might take 200–500ms. A checkout with 3 items involves ~13 network requests. That is 2.6–6.5 seconds of pure network latency, before any processing time. Any single failed request causes the entire checkout to stall or abort.

The submit → confirm → ship → deliver sequence is particularly unnecessary for a physical store: by the time the customer is at the kiosk, all four transitions happen within seconds of each other. This 4-step sequence was designed for the general order lifecycle (where days pass between submission and delivery). For a kiosk, a single "checkout" transition (or at most two: confirm + deliver) would suffice.

---

#### Inefficiency 2 — No Stock Reservation Model

**What:** There is no concept of "reserving" stock while a customer is building their cart. Stock is only deducted when the order is confirmed (step 9 of 10 in the checkout sequence).

**Consequence:** Two customers can add the last item to their respective carts simultaneously, both see it as available, both proceed through the checkout flow, and the conflict is only detected (or not, per Risk 6 above) at the confirm step. The customer who loses the race gets an error after completing the payment intent. There is no mechanism to notify the first customer's kiosk that stock was depleted by a concurrent session.

---

#### Inefficiency 3 — Shared Service Account Throttle Budget

**What:** All kiosk stations share one service account and therefore one authenticated-user throttle bucket (600 req/min global). The `OrderTransitionRateThrottle` (60/min) is also per-user, meaning all stations together are capped at 60 order transitions per minute.

**Consequence:** In a multi-station deployment during a busy period (e.g., a sale event), a single station firing multiple rapid transitions (or a retry storm after a network blip) consumes the shared budget. Other stations begin receiving `429 Too Many Requests` with no way to distinguish which station caused the exhaustion.

---

#### Inefficiency 4 — Customer Identity via Derived Email Is Fragile

**What:** The kiosk identifies returning customers by constructing a deterministic email from their Cédula (`v-12345678@kiosk.retailops.local`) and running `GET /api/v1/customers/?search=<email>`. The `search` filter on the customers endpoint is a general-purpose text search across multiple fields — it is not a direct unique-key lookup.

**Consequence:** The search may return multiple results if partial-match logic finds other records that happen to match. A dedicated lookup endpoint (`GET /api/v1/customers/by-cedula/?value=V-12345678`) with an indexed unique field would be O(1) instead of O(n) and unambiguous. As the customer table grows, the general search degrades.

---

#### Inefficiency 5 — Per-Item Stock Guard Is N+1 Requests

**What:** The pre-checkout stock guard fetches `GET /api/v1/products/{id}/` individually for each item in the cart.

**Consequence:** A cart with 5 different products requires 5 sequential API calls before checkout can proceed. There is no batch products endpoint (`GET /api/v1/products/?id__in=1,2,3,4,5`) in the current API design. This is an N+1 query pattern at the HTTP layer.

---

#### Inefficiency 6 — Hardcoded Exchange Rate With No Refresh Mechanism

**What:** `USD_TO_BS_RATE` is a literal number in the config block injected at deploy time. The optional `EXCHANGE_RATE_API_URL` field is provided for live rates, but fetching from a third-party rate API from a browser-based kiosk introduces additional CORS requirements against an external domain, and the kiosk design makes no provision for periodic background refresh.

**Consequence:** The displayed price in Bs. can diverge from the actual rate as soon as the config is deployed. In a high-inflation environment (which the Bolívar context suggests), this can be significant within hours. The only fix is to re-deploy `index.html` with an updated rate. There is no API endpoint on RetailOps that serves the live exchange rate, nor a mechanism for the kiosk to pull it without an external dependency.

---

#### Inefficiency 7 — Token Validity Not Verified at Startup

**What:** On startup, the kiosk authenticates unconditionally — always `POST /api/v1/auth/token/`, always creates a new token. If the kiosk page is reloaded (F5, network recovery reload), it generates another token. DRF's `Token` model is one-token-per-user — a new token replaces the previous one, immediately invalidating any other station still using the old token.

**Consequence:** In a multi-station deployment where stations share one account, a page reload on one station revokes the token for all other stations simultaneously. They receive `401 Unauthorized` on their next request and must each independently re-authenticate. This is the same blast-radius problem as Risk 2, now triggered by something as mundane as a network hiccup causing a browser reload.

---

#### Inefficiency 8 — Order State Held Only in sessionStorage

**What:** After creating an order (step 7), the kiosk stores `order_id` and `order_number` in `sessionStorage`. All subsequent steps use these values. The cleanup logic (idle timeout, cancel) also reads from sessionStorage.

**Consequence:** If the browser page reloads mid-checkout — due to a JS crash, memory pressure on a tablet, or explicit user action — `sessionStorage` is preserved on most reload paths but cleared on some (hard reload, navigation away, browser crash). The order exists on the server but the kiosk has lost track of it. It cannot resume the checkout, and the cleanup logic cannot run because it has no `order_id`. The order sits in Draft or Pending state indefinitely until a staff member manually cancels it, blocking that stock.

---

### Summary Table

| # | Category | Risk/Issue | Severity |
|---|---|---|---|
| 1 | Security | Service account password in static HTML | Critical |
| 2 | Security | Single shared account across all stations | Critical |
| 3 | Security | Manager role grants far more than needed | High |
| 4 | Security | DRF tokens never expire; idle timeout doesn't revoke | High |
| 5 | Security | Customer PII (Cédula) in unprotected notes field | High |
| 6 | Security | TOCTOU race: no server-side stock guard at confirm | Medium |
| 7 | Security | CORS allows HTTP origins; DEBUG bypass | Medium |
| 8 | Security | Client-generated payment reference numbers | Low |
| 9 | Architecture | 10 sequential API calls per checkout (no batching) | High |
| 10 | Architecture | No stock reservation model | High |
| 11 | Architecture | Shared account consumes shared throttle budget | Medium |
| 12 | Architecture | Customer lookup via general search (not indexed lookup) | Medium |
| 13 | Architecture | N+1 HTTP requests for pre-checkout stock guard | Medium |
| 14 | Architecture | Exchange rate hardcoded in static config | Medium |
| 15 | Architecture | Page reload revokes token for all stations | Medium |
| 16 | Architecture | Order state in sessionStorage lost on reload | Medium |
