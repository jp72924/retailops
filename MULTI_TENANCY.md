# Multi-Tenancy Feasibility Study

What it would take to serve RetailOps as a multi-tenant service - one system serving many
independent retail businesses - and which isolation model to adopt.

This is a decision document. It records a recommendation and the reasoning behind it. No
code has been changed as a result of writing it.

**Companion reading:** `ARCHITECTURE.md` describes the system as it exists today. This
document assumes that context.

## How to read the claims in this document

| Marking | Meaning |
|---|---|
| **Verified** | Checked against source at the cited `file:line` while writing this document. |
| **Projected** | An estimate or consequence derived from verified facts. Reasoned, not measured. |

Every structural claim in sections 2, 3 and 4 is verified. Effort and cost claims in
sections 5 onward are projected.

**Assumptions confirmed with the project owner:**

- Target scale is **tens to low hundreds of tenants** - independent retail businesses, each
  running one to ten kiosk stations.
- **Requiring PostgreSQL in local development and CI is acceptable.**

Both materially shape the recommendation. If either changes, section 6 changes with it.

---

## 1. Scope and current state

RetailOps is single-tenant today: one deployment serves one business. There is no partial
tenancy to build on. **Verified:** the codebase contains no tenant, organization, company,
or branch concept in any form; no `django-tenants`; no `DATABASE_ROUTERS`; no
`threading.local`, `contextvars`, or `get_current`-style ambient context; no `CACHES`
configuration; a single `DATABASES['default']`; and no deployment automation beyond CI
running `check`, `makemigrations --check`, and `test` against SQLite.

Every query in the application is a flat global-namespace query.

The one store-shaped concept is `KioskStation.store_identifier` (`core/models.py:776`), a
free-text `CharField(max_length=50)` with no uniqueness, no foreign key, and no registry.
**Verified: it is never used to filter a single query.** It appears in `unique_together`
(`core/models.py:797`), in the derived service-user email
(`api/kiosk/provisioning.py:44`), and in Django admin `list_display`/`list_filter`. It is a
naming convention for hardware, not a tenancy axis, and it cannot be promoted in place.

### The query surface, counted

**Verified** counts of sites that read or write business data without any ownership filter:

| Location | Count | Shape |
|---|---:|---|
| `core/views.py` | 52 `.objects.` + 26 `get_object_or_404(` = **78** | 43 function-based views across 47 routes. No shared base class. |
| `api/views/*.py` | **23** | 9 collection querysets (4 `get_queryset()` overrides + 5 class-level `queryset =`) plus ad-hoc manager calls inside actions. |
| `api/kiosk/views.py` | **10** | All plain `APIView`. No queryset layer to hook. |
| `api/serializers/*.py` | **8** write-side `PrimaryKeyRelatedField(queryset=...)` + 13 uniqueness checks | See section 8. |
| `core/admin.py` | 11 `ModelAdmin`, **0** `get_queryset()` overrides | Plus unfiltered `raw_id_fields` lookups. |

Call it **roughly 150 sites** in total. The distribution matters more than the sum, and
section 6 explains why.

---

## 2. Blocker inventory

Ranked by launch-blocking severity - what stops you shipping, and what loses money after
you ship. All entries **verified**.

### 2.1 Unscoped recipient matching - a fraud vector, not a leak

`api/kiosk/views.py:537-541`:

```python
if settings.recipient_validation_enabled:
    profiles = RecipientProfile.objects.filter(
        is_active=True, payment_method=payment_method,
    )
    recipient_match = match_recipient_profile(vepay_data, payment_method, profiles)
```

`RecipientProfile` is the allowlist of bank accounts the business will accept payment into.
On a shared schema this query returns **every tenant's** accounts. A receipt showing payment
into tenant A's account would validate as a legitimate payment at tenant B's kiosk, and the
customer would walk out with goods.

This is not a data-disclosure problem. It is direct inventory loss, available to anyone who
knows any tenant's published payment details - which are, by design, published to customers
on the kiosk screen. The same unscoped read appears in `SystemSettings.clean()`
(`core/models.py:602`), so a tenant could enable recipient validation while owning zero
profiles of its own.

It ranks first because the constraint problems below stop you from launching, and this one
loses money quietly after you launch.

### 2.2 A global row lock held across third-party HTTP

Verified in full in section 3. Today it serialises all order creation; on a shared schema it
becomes a cross-tenant denial of service.

### 2.3 `SystemSettings` is a destructive singleton

`core/models.py:611-613`:

```python
def save(self, *args, **kwargs):
    self.pk = 1
    super().save(*args, **kwargs)
```

The primary key is overwritten **unconditionally**. There is no guard and no exception: a
second tenant's `SystemSettings(...).save()` silently overwrites the first tenant's row.
That row holds the currency configuration, the live secondary exchange rate, the OCR
provider base URL and **`ocr_api_key`**, receipt retention policy, and
`recipient_validation_enabled`.

This ranks first *by ordering* - nothing else works until it is resolved - but **not by
cost**. All ~35 call sites funnel through one classmethod (`core/models.py:618-621`):

```python
@classmethod
def get(cls):
    obj, _ = cls.objects.get_or_create(pk=1)
    return obj
```

Keeping that zero-argument signature and backing it with ambient context means the context
processor, the `regional.py` template tag on every rendered money value, `VEPayClient`, and
`Model.clean()` need no changes at all. The apparent size of this blocker is an artifact of
counting call sites instead of chokepoints.

Worth noting separately: `get()` performs a `get_or_create` on a **read** path executed on
every template render. That is an INSERT attempt on read, which will break under a
read-replica router and can race. Creation belongs in provisioning.

### 2.4 Identity: `User.email` and the single `Role` foreign key

`core/models.py:89` declares `email = models.EmailField(unique=True)` and it is the
`USERNAME_FIELD`. `role` is a single-valued FK (`core/models.py:92-98`) to a `Role` whose
`name` is itself globally unique (`core/models.py:57`).

One human therefore cannot work for two tenants, and cannot hold different roles at each.

**The important part is not the constraint.** Scoping the unique index is insufficient: the
**authentication backend must also filter by the current tenant**, or a user from tenant A
authenticates successfully against tenant B's subdomain. A mistake here logs someone into
the wrong company. This deserves its own deployment and its own tests.

### 2.5 Global uniqueness that collides in normal use

**Verified**, all in `core/models.py`: `Product.sku` (`:229`), `Customer.email` (`:137`),
`Customer.national_id` (`:138`), `ProductCategory.name` (`:167`), `SequenceCounter.prefix`
(`:24`), and `Payment.transaction_key` as a partial unique (`:456-460`).

Generic SKUs, shared EAN codes, a category named "Beverages", the same shopper's national ID
at two stores - all collide immediately.

`Payment.transaction_key` carries an extra hazard. `api/kiosk/views.py:557`:

```python
if transaction_key and Payment.objects.filter(transaction_key=transaction_key).exists():
    raise _DuplicateTransactionError(transaction_key)
```

Unscoped, this is a **cross-tenant existence oracle**: one tenant can probe for another
tenant's payment references by trial submission and read the answer from the error code.

### 2.6 The dashboard leaks revenue and customer names

`api/views/dashboard.py:83-124` contains **five unscoped queries in one method**: order
count for the month (`:87`), revenue `Sum` (`:91-98`), pending-payment count (`:102`),
low-stock count via a `Product` annotation (`:109-118`), and the five most recent orders
(`:120-124`) - which are serialised with `order.customer.get_full_name()`.

On a shared schema, `GET /api/v1/dashboard/` would hand every tenant the whole installation's
revenue and a sample of competitors' customer names.

### 2.7 Media path routing

Upload paths carry no tenant segment: `products/{YYYY}/{MM}/...` (`core/models.py:202`) and
`receipts/{YYYY}/{MM}/...` (`core/models.py:403`). Beyond the absence of isolation, the
human-readable segment leaks across tenants - a receipt object key embeds another tenant's
order number.

The fix has a trap, described precisely in section 4.

---

## 3. A pre-existing bug, independent of tenancy

This section documents a defect in the current single-tenant system. It is worth fixing on
its own merits and it must be fixed before any shared-schema tenancy work.

**Verified chain:**

```mermaid
sequenceDiagram
    participant V as View
    participant DB as PostgreSQL
    participant OCR as VEPay

    V->>DB: BEGIN  (api/kiosk/views.py:279)
    V->>DB: SELECT FOR UPDATE products  (:287)
    V->>DB: order.save()  (:323)
    Note over V,DB: SequenceCounter.next_value('SO-YYYYMMDD')<br/>core/models.py:35 opens transaction.atomic()<br/>→ degrades to SAVEPOINT (outer block open)<br/>→ SELECT FOR UPDATE holds until OUTER commit
    V->>OCR: _payment_receipt_fields (:363) → parse_receipt (:502)
    Note over OCR: requests.post timeout=30s<br/>2 attempts, sleep(1) between<br/>worst case 61s
    OCR-->>V: response (or timeout)
    V->>DB: COMMIT — lock finally released
```

Each link, verified:

- `api/kiosk/views.py:279` opens `with transaction.atomic():` around the entire checkout.
- `:323` calls `order.save()`, which triggers `SalesOrder._generate_order_number()` and thus
  `SequenceCounter.next_value('SO-YYYYMMDD')`.
- `core/models.py:34-40` - `next_value` opens its own `transaction.atomic()`. **Because an
  outer atomic block is already open, this degrades to a savepoint**, so the
  `SELECT FOR UPDATE` row lock at `:37` is held until the *outer* transaction commits, not
  until `next_value` returns.
- `:363` then calls `_payment_receipt_fields(...)`, still inside the outer block.
- `:502` calls `async_to_sync(VEPayClient().parse_receipt)(...)`.
- `core/services/vepay.py:95` issues `requests.post(..., timeout=self.timeout)` where
  `self.timeout = settings.ocr_timeout_seconds`, **default 30** (`core/models.py:565`).
- `core/services/vepay.py:93` wraps this in `for attempt in range(2)` with
  `time.sleep(1)` between attempts (`:123`).

**Worst case: 30 + 1 + 30 = 61 seconds of third-party HTTP with a global row lock held.**

Because the `SO-{today}` counter row is shared by every order created that day, this
serialises *all* order creation behind whichever checkout is currently waiting on VEPay.
Single-tenant, that is a self-inflicted throughput ceiling and a latency cliff whenever the
OCR provider degrades. On a shared-schema multi-tenant deployment it becomes a cross-tenant
denial of service: one tenant's OCR provider having a bad afternoon stalls checkout for every
other tenant on the box.

**Fix:** move OCR and receipt validation *before* the transaction. Parse and validate the
receipt, then open `transaction.atomic()`, re-acquire the product locks, re-validate stock,
and write. This preserves the atomicity guarantee that
`konteo-express/CLAUDE.md` depends on - checkout remains a single call that either fully
succeeds or fully fails - while removing network I/O from the critical section.

---

## 4. Two claims to state precisely

Both of these are easy to overstate, and an overstated claim discredits the rest of a
document once a reader checks it.

### 4.1 The storage prefix trap

`retailops/storage.py:125-131` routes objects to one of three backends by **leading string
match**:

```python
def _storage_for_name(self, name):
    normalized = str(name or '').replace('\\', '/').lstrip('/')
    if normalized.startswith(self.product_prefixes):   # ('products/',)
        return self.product_storage
    if normalized.startswith(self.receipt_prefixes):   # ('receipts/',)
        return self.receipt_storage
    return self.default_storage
```

The obvious tenant-prefixing change - `t/acme/receipts/...` - makes both tests fail, so the
object falls through to `default_storage`.

**The accurate consequence:** receipts are silently demoted from the receipt storage profile
to the default profile. That means a **different bucket** when three are configured
(`MEDIA_GCS_RECEIPT_BUCKET_NAME` vs `MEDIA_GCS_DEFAULT_BUCKET_NAME`), with whatever
lifecycle and retention policy was tuned for general media rather than for payment evidence,
and **without** the `private, no-store` cache-control that the receipt profile applies.

It is **not** unconditionally public. `default_querystring_auth` defaults to `True`
(`retailops/settings.py:349`), so such objects would still be signed under default
configuration. Exposure is config-dependent: it requires
`MEDIA_GCS_DEFAULT_SIGNED_URLS=False`.

**The conclusion is unchanged and the fix is cheap:** the tenant segment goes *after* the
type prefix - `products/{tenant}/{YYYY}/{MM}/` and `receipts/{tenant}/{YYYY}/{MM}/` - which
requires no change to `storage.py` at all.

A migration note that is easy to miss: `_storage_for_name` drives `_open`, `_save`,
`delete`, `url`, and `exists`. Changing the scheme for *existing* files means physically
copying blobs, not rewriting database paths. The way to avoid that entirely is described in
Phase 4.

### 4.2 Late binding is not a kiosk special case

It is tempting to frame kiosk authentication as the one awkward path. The real distinction
is between two sources of tenant identity:

| Source | Available where | Used by |
|---|---|---|
| **Request envelope** - `Host` header, or an explicit header | Middleware, before any authentication | Back-office, CLI, MCP |
| **Credential** - the `KioskKey` API key | Only after DRF authentication runs, which is *after* all middleware | Kiosk stations |

The kiosk falls into the second bucket because a station is a fixed-configuration device in
a shop, not a browser following a link. Framing it this way makes a two-phase resolver
obviously necessary rather than a special case bolted on, and it generalises to any future
credential-derived scheme.

---

## 5. Isolation models evaluated against this codebase

### (a) Shared database, shared schema, tenant foreign key

Add a `tenant` FK to all 14 models, convert every global unique to a composite, and filter
every query.

**What it costs here.** The ~150 sites, and it is not a one-time cost. Every future pull
request that adds a query is a potential cross-tenant breach. Nine unique constraints need
concurrent index swaps with `SeparateDatabaseAndState` reconciliation to keep
`makemigrations --check` green in CI.

**What it solves.** One database, one `migrate`, one connection pool, one backup. Crucially,
**cross-tenant reporting stays a single query** - "orders across all customers this month",
which is a question you will want to answer about your own product. Under every other model
that becomes a rollup job. This advantage is real and routinely underpriced.

**What it does not solve.** Blast radius is total: one missing `.filter(tenant=)` exposes
everything. Per-tenant restore means restoring the whole database to a scratch instance and
copying rows back in foreign-key order across 14 tables - a bespoke script you will write
once and exercise for the first time during an incident.

### (b) Shared database, schema per tenant

One PostgreSQL schema per tenant, selected by `search_path`.

**Verified blockers that disappear entirely, with no code changes:**

| Blocker | Fate |
|---|---|
| 2.1 Unscoped recipient matching | **Gone.** The query sees only its own schema. |
| 2.3 `SystemSettings` `pk=1` | **Gone - becomes correct by construction.** One row per schema. `self.pk = 1` is now right. Zero of the ~35 call sites change. |
| 2.4 `User.email` / `Role.name` uniques | **Gone** as constraints. The design question - can one human serve two tenants - remains a product decision. |
| 2.5 All global uniques | **Gone.** Zero migrations. |
| 2.5 `transaction_key` existence oracle | **Gone.** |
| ~150 unscoped query sites | **All correct, untouched.** |

Three consequences deserve emphasis because they are not obvious:

**The 8 write-side `PrimaryKeyRelatedField(queryset=...)` vectors become safe for free.**
Under (a) these are genuinely awkward: the `QuerySet` is lazy but the *manager's*
`get_queryset()` runs at import time, so an ambient-context-reading manager would freeze a
`tenant IS NULL` clause into the field, forcing a `get_fields()` override or a custom field
on all eight. Under (b) nothing is frozen - `search_path` binds at SQL **execution** time.
`Product.objects.all()` at `api/serializers/inventory.py:76` and the
`__import__('core.models', fromlist=['Customer']).Customer.objects.all()` hack at
`api/serializers/order.py:79` both become correct as written.

**The `_stock` JOIN aggregate stops being a problem.** `Sum('inventory_movements__quantity')`
is a compiler-built JOIN that no manager intercepts. Same schema, both tables, correct.
`api/filters.py:28-39` keeps its hard dependency on the `_stock` alias with no accommodation.

**`pg_dump -n acme` and `DROP SCHEMA acme CASCADE`** turn per-tenant backup, export, and
deletion from bespoke scripts into one-liners.

**What it costs.**

- PostgreSQL in development and CI. `_sqlite_database_config` is currently the default branch
  and `retailops/test_runner.py` would need rework. *(Accepted by the project owner.)*
- `INSTALLED_APPS` splits into shared and tenant sets. `core` holds `User`,
  `SystemSettings`, and `KioskStation`, so `core` goes wholly tenant-side.
- `migrate_schemas` runs N times. At tens-to-low-hundreds this is minutes.
- **PgBouncer must run in session pooling mode.** `SET search_path` does not survive
  transaction-scoped connection reuse, and the failure is silent and catastrophic. Settings
  already expose `CONN_MAX_AGE`, so pooling is clearly anticipated. This is a hard
  operational constraint that belongs in the runbook, not in a postmortem.
- `search_path` is a *default*, not a *restriction* - schema-qualified raw SQL bypasses it.
- Cross-tenant reporting for you as the vendor becomes a rollup job. `OcrCallLog` is the
  concrete case: billing OCR usage across tenants now needs infrastructure.

**The one place (b) genuinely hurts: kiosk authentication.**

`api/kiosk/authentication.py:39-45` resolves a station by querying `KioskStation` - but that
table lives in a tenant schema, and you cannot know which schema without first finding the
station. Chicken and egg.

The resolution is a small public-schema index: `public.KioskKeyIndex(api_key_hash, tenant)`.
Authentication queries public for the hash, enters the tenant schema, then verifies
`is_active` and loads the station.

**Key it on the full SHA-256 hash, not the 8-character prefix.** `api_key_prefix` is
`db_index=True` but *not* unique (`core/models.py:779`); today the lookup disambiguates by
passing both prefix and hash, and a prefix-keyed public index would reintroduce exactly the
ambiguity the current code avoids. The hash discloses nothing.

Cost: roughly 40 lines and one public model, plus the requirement that key rotation and
deactivation write both places atomically. Everything already routes through
`api/kiosk/provisioning.py`, which is already transactional; the admin bulk actions in
`core/admin.py:158-178` would need the same treatment. This is the only structural cost (b)
imposes that (a) does not.

### (c) Database per tenant

Everything (b) solves, plus true blast-radius isolation and per-tenant restore that is an
actual restore.

Disqualified on operations, not on design. N databases to migrate, with no Dockerfile, no
compose file, no deployment automation, and CI that currently runs on SQLite. Migration
orchestration is not a detail here - it is the entire job. Secondary: PostgreSQL
`max_connections` becomes the ceiling (workers x tenants x `CONN_MAX_AGE`), and Django
cannot express foreign keys across databases, so nothing can reference the `Tenant` model.

Reasonable later as a premium tier for a regulated customer. Wrong as the primary model now.

### (d) Instance per tenant (silo)

**Zero code changes.** Ship the current codebase N times with N environment files. Every
blocker in section 2 evaporates: `pk=1` is correct, all 150 sites are correct, kiosk auth is
unchanged.

The honest framing, which usually gets skipped: silo does not eliminate engineering, it
**relocates it from Django to operations**. Cost is N x (application server + database +
buckets + TLS certificate + configuration + *upgrade*), linear in N with no economies of
scale. For a team that has not yet written a Dockerfile, that is a bad long-run trade.

But it has one property nothing else has: **a coding mistake cannot cause a cross-tenant
breach.** For a system handling bank transfer receipts and OCR-verified payments, that is
worth real money.

Its correct role is a **bridge, not a destination**.

### Blocker fate summary

| Blocker | (a) discriminator | (b) schema | (c) database | (d) silo |
|---|---|---|---|---|
| Unscoped recipient match (fraud) | Scope the query | **Gone** | Gone | Gone |
| Sequence lock across HTTP | Fix separately | Fix separately | Fix separately | Fix separately |
| `SystemSettings` `pk=1` | Fix `get()` (hours) | **Gone - correct** | Gone | Gone |
| `User.email` + auth backend | Composite + **custom backend** | Gone as constraint | Gone | Gone |
| Global uniques (5) | 5 concurrent index swaps | **Gone** | Gone | Gone |
| Dashboard aggregates | Scope 5 queries | **Gone** | Gone | Gone |
| Media path routing | Type-prefix-first | Same fix | Same fix | Per-tenant buckets |
| ~150 unscoped sites | **The entire project** | **Zero edits** | Zero edits | Zero edits |
| 8 import-time related fields | `get_fields()` on all 8 | **Gone - lazy binding** | Gone | Gone |
| `_stock` JOIN aggregate | Needs RLS to be safe | **Gone** | Gone | Gone |
| Kiosk late binding | **Easiest here** | Public key index (~40 lines) | Registry + router | N/A |
| Per-tenant backup / delete | Bespoke, fragile | **`pg_dump -n` / `DROP SCHEMA`** | Trivial | Trivial |
| Vendor cross-tenant reporting | **Trivial** | Rollup job | Warehouse | Warehouse |
| Ops cost at N=20 | Lowest | Low | High | **Prohibitive** |

---

## 6. Recommendation

**Adopt schema-per-tenant on PostgreSQL, with a public-schema kiosk key index. Use manual
instance-per-tenant deployments for the first three to five customers as a bridge. Keep the
shared-schema discriminator model as the documented fallback.**

The argument is one asymmetry, and it is worth stating plainly rather than burying it in the
matrix above:

> **78 of the ~150 unscoped query sites are in `core/views.py`**, spread across 43
> function-based views with no shared base class, no `get_queryset()`, and no permission
> hook. Under the discriminator model those are 78 individually-reviewed edits *and* a
> permanent per-pull-request security obligation. Under schema-per-tenant they are correct
> without being touched.

That converts an unbounded recurring cost into a bounded one-time cost, which is the single
most valuable property for a small team.

Supporting reasons, in order of weight:

1. **The highest-ranked structural blocker becomes free.** `SystemSettings`'s
   `self.pk = 1` - the line that would silently overwrite one tenant's OCR API key and
   exchange rate with another's - is not *fixed* under schema-per-tenant, it is *correct*.
   So are the context processor on every template render, the `regional.py` template tag on
   every money value, `VEPayClient.__init__`, and `SystemSettings.clean()`.

2. **Blockers 2.4 through 2.6 are constraint-shaped, and constraints are per-schema.** Nine
   composite-index migrations, each needing a concurrent-create/drop sequence and migration
   state reconciliation, simply do not happen.

3. **Scale fits.** Tens to low hundreds puts `migrate_schemas` in the minutes range. The
   regime where schema-per-tenant degrades - thousands of tiny tenants - is not the target
   market.

4. **Per-tenant export, backup, and deletion come nearly free.** Under the discriminator
   model those are three bespoke scripts, written under time pressure and tested never.

5. **The kiosk survives intact.** The key-index workaround preserves the property that
   Konteo Express needs zero changes. Those are physical terminals in shops; a configuration
   change means a site visit.

**The decision hinge, stated honestly.** The strongest counter-argument is not technical
elegance - it is that (b) forces PostgreSQL into development and CI, adds a third-party
dependency to the Django upgrade path, and hard-constrains connection pooling to session
mode. The PostgreSQL requirement has been accepted; the other two are real and ongoing.

**A middle path worth costing.** Schema-per-tenant **without `django-tenants`**: roughly 150
lines of middleware issuing `SET search_path`, plus a `provision_tenant` command wrapping the
existing `OperationalSiteInitializer`. You get the isolation, drop the dependency and its
version-compatibility risk, and pay by writing your own migration orchestration for N
schemas. Given how much of `django-tenants` would be bypassed anyway - custom kiosk
authentication, custom provisioning - this is more attractive than it first appears. Cost
both before committing.

---

## 7. Tenant resolution design

### Use `contextvars`

Not thread-locals, not a request attribute as the source of truth, not explicit parameter
passing. The reasons are specific to this codebase.

**Explicit passing is structurally impossible.** The consumers are ambient call sites with
no request in scope:

- `VEPayClient.__init__` calls `SystemSettings.get()` with no arguments
  (`core/services/vepay.py:65`) and is constructed as a bare `VEPayClient()` at
  `api/kiosk/views.py:502`.
- `Model.clean()` is called by Django **with no arguments**. `SystemSettings.clean()`
  (`core/models.py:602`) reads `RecipientProfile`. You cannot pass a tenant into it.
- `core/templatetags/regional.py` runs during template rendering on every money value.
- `purge_receipts` and `update_bcv_rate` are management commands with no request at all.

**Thread-locals are actively unsafe here.** `asgi.py` exists, and
`async_to_sync(VEPayClient().parse_receipt)` is already in the checkout path with
`asyncio.to_thread` inside `parse_receipt`. `asyncio.to_thread` explicitly performs
`contextvars.copy_context()` and runs the target via `ctx.run(...)`: **context variables
propagate across that hop by design; thread-locals do not.** Under ASGI it is worse - the
thread pool reuses threads across requests, so a leaked value becomes a cross-tenant read.

`request.tenant` remains a useful convenience mirror for templates and views. It is not the
source of truth.

### The module

`core/tenancy.py` exposing a `ContextVar`, `get_current_tenant()`, `set_current_tenant()`
returning a `Token`, `reset_current_tenant(token)`, and a `tenant_context(t)` context
manager that resets in a `finally`.

**Always reset with the `Token`, never by setting `None`.** `ContextVar.reset(token)`
restores the *previous* value, which is what makes nesting work - and nesting will happen, in
`provision_tenant` and in every management command that loops tenants.

### Two-phase resolution

```mermaid
flowchart TD
    A["Request arrives"] --> B["TenantMiddleware (position 2)"]
    B --> C{"Host or header<br/>resolves a tenant?"}
    C -->|yes| D["set_current_tenant(t) → Token"]
    C -->|no| E["set_current_tenant(None) → Token"]
    D --> F["SessionMiddleware → AuthenticationMiddleware"]
    E --> F
    F --> G["View dispatch"]
    G --> H{"DRF KioskTokenAuthentication?"}
    H -->|yes| I["public.KioskKeyIndex lookup by full hash<br/>→ enter schema → set tenant"]
    H -->|no| J["Tenant already set by middleware"]
    I --> K["View executes"]
    J --> K
    K --> L["finally: reset_current_tenant(token)"]
```

**Middleware position is a hard constraint, not a preference.** Under schema-per-tenant the
`User` table is schema-local, so resolving `request.user` itself requires `search_path` to be
set. `TenantMiddleware` must therefore precede **both** `SessionMiddleware` and
`AuthenticationMiddleware` - position 2 in `MIDDLEWARE`, immediately after
`SecurityMiddleware`, where `KioskCORSMiddleware` currently sits.

`ALLOWED_HOSTS` needs no code change: Django's leading-dot wildcard means
`DJANGO_ALLOWED_HOSTS=.retailops.app` covers every tenant subdomain, and the existing CSV
parsing (`retailops/settings.py:69-74`) passes it through unchanged.

**The teardown problem, and the trick that solves it.** DRF's authentication layer has no
teardown hook - nothing unwinds what `authenticate()` sets. The fix is that **the middleware
always establishes a Token, even when it resolves no tenant**, and always resets it:

```python
token = set_current_tenant(resolved_or_None)
try:
    return self.get_response(request)
finally:
    reset_current_tenant(token)
```

The outermost frame owns the lifetime, so anything DRF sets deeper in the stack is discarded
on the way out. No DRF lifecycle changes, no signal hooks, and it generalises to any future
credential-derived scheme.

**Harden the existing pattern while you are there.** `RegionalMiddleware`
(`core/middleware.py:83-97`) already does exactly this shape - activate request-scoped state,
deactivate on the way out - and the authors clearly understood the bleed hazard. But it calls
`timezone.deactivate()` after `get_response` **without** a `finally`. In Django's normal
chain `convert_exception_to_response` usually makes this hold; a middleware above it raising,
or a test driving it via `RequestFactory`, leaks the state. For a timezone that is cosmetic.
For a tenant it is a cross-tenant exposure. The pattern is 90% right and needs the last 10%.

### An invariant nothing currently enforces

If a request arrives at `acme.retailops.app` carrying a `KioskKey` belonging to `bodega`,
**reject it with 401.** Do not let the credential silently override the envelope. There is
currently no place this could even be checked. It is roughly five lines in `authenticate()`
and it converts a confused-deputy class of bug into a hard failure.

---

## 8. Enforcement: what each layer actually catches

Django's manager semantics decide which layers are necessary, and they are subtler than the
usual "add a default manager" advice.

| Access path | Filtered by a tenant-aware `_default_manager`? |
|---|---|
| `Model.objects.<anything>` | **Yes** |
| `get_object_or_404(Model, pk=...)` | **Yes** - uses `_default_manager` |
| Reverse FK / M2M managers (`order.items.all()`) | **Yes** |
| Forward FK attribute (`payment.sales_order`) | **No** - uses `_base_manager` |
| Relation spans in `filter()` / `annotate()` | **No** - compiler-built JOIN, no manager |
| `select_related()` | **No** |
| Raw SQL / `.extra()` | **No** |
| Cascade-delete collector | **No** - and correctly so |
| `PrimaryKeyRelatedField(queryset=...)` at import | **Frozen wrong** under (a) |

Two consequences change the effort estimate:

- **The 26 `get_object_or_404` sites in `core/views.py` are among the cheapest to fix, not
  the hardest** - a default manager catches all of them. That reduces the effective
  `core/views.py` burden under (a) from 78 to 52.
- **The "scope the root, trust the FK" strategy silently depends on tenant-consistent
  foreign keys, and nothing in the codebase enforces that.** This is a bigger finding than
  the `_stock` aggregate itself. A `Product` scoped correctly does constrain
  `Sum('inventory_movements__quantity')` transitively - *provided* no movement ever points at
  another tenant's product.

One asset worth naming: `api/views/product.py:17` `_annotated_products()` is a **single
function** owning the `_stock` annotation, and it is the ViewSet's only `get_queryset()`.
`api/filters.py`'s hard dependency on the `_stock` alias is satisfied by scoping one
function.

### The layers

| Layer | Catches | Misses | Verdict |
|---|---|---|---|
| **0. `search_path`** (option b) | Every row in the table above except schema-qualified raw SQL; all 78 `core/views.py` sites, all kiosk `APIView` queries, all 8 related fields, all 13 uniqueness checks, all 11 `ModelAdmin`s | Explicit `schema_context('public')`; schema-qualified raw SQL | **The primary control under (b)** |
| **1. Tenant-aware default managers** (option a) | Rows 1-3 | Rows 4-8 - where the residual risk lives | Necessary but insufficient under (a) |
| **2. Base ViewSet mixin** | The 9 collection querysets | The ad-hoc action queries (`order.py` `bulk_transition`, `inventory.py` `bulk_adjust`), all kiosk `APIView`s, all 78 `core/views.py` sites, all write-side related fields | **~6% of the surface. Never cite it as a control.** |
| **3. `has_object_permission`** | IDOR on detail routes after someone forgets to scope `get_queryset()` | All list routes, all `core/views.py` FBVs, the kiosk `APIView`s, every write through a related field | Narrow, but that is the *most likely* mistake, and it is ~20 lines. **Do it regardless of model.** |
| **4. PostgreSQL Row-Level Security** | Everything, including JOIN aggregates, `_base_manager` traversal, `select_related`, and raw SQL | Nothing at the data layer | Under (a) this is not defence-in-depth, it is **the** control |
| **5. Test-suite guard** | See section 9 | Nothing it is pointed at | **Cheapest high-value layer. Mandatory under every model.** |

Two design points that matter more than the manager itself, if (a) is ever chosen:

- **Do not set `Meta.base_manager_name` to a filtering manager.** Django explicitly
  discourages it; it breaks `select_related`, cascade deletes, and `refresh_from_db` in ways
  that produce silent data corruption rather than errors.
- **When the context is empty, raise - do not return `.none()`.** Silent empty results
  produce "the dashboard shows zero orders" bugs that get diagnosed as data problems. An
  exception produces a stack trace pointing at the missing context. Provide an explicit
  all-tenants escape hatch. Be aware this makes `manage.py spectacular`, `dumpdata`, and the
  seed command raise until they are wrapped.

**On Row-Level Security, lead with the failure mode.** `FORCE ROW LEVEL SECURITY` is the
whole ballgame:

```sql
ALTER TABLE core_salesorder ENABLE ROW LEVEL SECURITY;
ALTER TABLE core_salesorder FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON core_salesorder
  USING (tenant_id = current_setting('retailops.tenant_id')::int);
```

**Table owners bypass RLS by default.** If Django's database user owns the tables - which it
does if it ran the migrations - then without `FORCE`, RLS is enabled, looks correct in
`\d+`, and does nothing. Either force it or run migrations as an owner role and the
application as a separate restricted role. Test this explicitly: a test asserting "tenant B's
rows are invisible" that passes against a non-forced policy is the worst possible outcome.

Two frictions specific to this codebase: `ATOMIC_REQUESTS` is `False`, so most requests run
in autocommit and `SET LOCAL` has no transaction to scope to - you need a session-level `SET`
at connection checkout plus per-request reassertion. And with `CONN_MAX_AGE > 0` a stale
setting persists onto a reused connection, so it must be set on **every** request. This is
the same session-pooling constraint as `search_path`, so under (b) it is already paid for.

---

## 9. The test guard

The highest-leverage artifact in the entire project, and it should be built in Phase 0 -
against the single-tenant application with a stub tenant - so it is ready before it is
needed.

**The two-tenant sweep.** Create tenants A and B with structurally identical data. Enumerate
every route from `get_resolver().reverse_dict` - 47 back-office routes plus the API router -
and drive each one as a tenant-A user. Then assert:

1. No tenant-B primary key, SKU, email, national ID, order number, or customer name appears
   in any response body.
2. Every detail route returns **404 - not 403, and not 200** - for a tenant-B object id.

One mechanical implementation covers list routes, detail routes, the five dashboard
aggregates including the customer names at `api/views/dashboard.py:120-124`, the HTML
back-office, and the kiosk endpoints.

Three cheap companions:

- **Model census.** Iterate `apps.get_models()` and assert every `core` model either carries
  a tenant FK (under (a)) or appears in an explicit exemption set. Under (b), assert the
  shared and tenant app sets partition `INSTALLED_APPS`. This fails the day someone adds a
  fifteenth model - the regression that *will* happen.
- **Cross-tenant FK integrity.** For every foreign key between tenant-scoped models, assert
  `source.tenant_id == target.tenant_id`. This protects the assumption the whole strategy
  rests on and that nothing currently enforces.
- **SQL predicate wrapper.** Via `connection.execute_wrapper` in tests, assert every
  statement against a scoped table carries a tenant predicate. Cruder and noisier than the
  sweep, but it reaches paths the sweep cannot - management commands and background jobs.

If (a) is ever chosen, the rigorous version of the FK check is structural rather than
tested: `UNIQUE(id, tenant_id)` on each parent plus a composite
`FOREIGN KEY (parent_id, tenant_id)` on each child. Django cannot express composite foreign
keys but `RunSQL` can. That is ~14 extra unique indexes and ~20 composite FKs, and it makes
cross-tenant corruption *impossible* rather than *detected later* - the correct answer for a
payments-adjacent system on a shared schema.

---

## 10. Phased migration

Every phase ships. Nothing sits on a long-lived branch.

### Phase 0 - Hygiene (valuable single-tenant, ship independently)

1. **Fix the lock-across-network-I/O bug** (section 3). Move OCR and receipt validation
   outside the checkout transaction. This removes a 61-second worst-case global lock hold
   from the hottest path *today*, and removes the cross-tenant DoS from the future before
   tenants exist.
2. **Configure `CACHES`.** Currently absent, so throttling uses per-process `LocMemCache`:
   the `600/min` global ceiling is really `600 x worker_count` and resets on every deploy.
   Per-tenant throttling is impossible until a shared cache exists, and the VEPay circuit
   breaker in section 11 needs it too.
3. **Add PostgreSQL to CI** as a matrix leg.
4. **Write the two-tenant sweep harness** against the single-tenant app.
5. **Add `has_object_permission` backstops** - no-ops today, live later.

### Phase 1 - Tenant model and resolution, no enforcement

`public.Tenant(slug, name, schema_name, is_active, created_at)` and
`Domain(host, tenant, is_primary)`. **Design `Domain` in now** even though custom domains are
a year away; retrofitting means touching the resolver a second time.

Add `core/tenancy.py`, `TenantMiddleware` at position 2 with `try`/`finally` Token
discipline, the kiosk phase-2 resolution, and the envelope/credential agreement check.

Ships with exactly one tenant configured. Behaviour is byte-identical. **Shippable.**

### Phase 2 - Data migration

**Under (b):** `CREATE SCHEMA default_tenant`, then `ALTER TABLE ... SET SCHEMA` per table.
These are **metadata-only operations**, so the maintenance window is seconds to minutes, not
hours. Create the `public.Tenant` row. This is the one genuinely disruptive step and it is
small.

**Under (a),** for reference: add `tenant` as nullable (no table rewrite on PostgreSQL 11+),
backfill in batches, then `SET NOT NULL` **via** a `NOT VALID` CHECK constraint plus
`VALIDATE CONSTRAINT` - which takes only a `SHARE UPDATE EXCLUSIVE` lock - rather than
naively, which takes `ACCESS EXCLUSIVE` for a full table scan.

Unique constraints cannot be replaced atomically, so per constraint:
`CREATE UNIQUE INDEX CONCURRENTLY` (needs `RunSQL` in a migration with `atomic = False`, or
`AddIndexConcurrently`; `AddConstraint` will not do it) → deploy code that no longer depends
on the global unique → `DROP INDEX CONCURRENTLY` → **reconcile migration state with
`SeparateDatabaseAndState`**, which is non-negotiable because CI runs
`makemigrations --check`.

**`User.email` deserves its own sub-phase**, because of the authentication-backend issue in
section 2.4. Isolate it, test it specifically, deploy it alone.

**Shippable.**

### Phase 3 - Enforcement

**Under (b):** flip the middleware to actually issue `SET search_path`. All ~150 sites become
correct simultaneously. Run the sweep. This is the payoff, and it is one commit.

**Under (a):** add managers model by model, highest blast radius first - `SalesOrder`,
`Payment`, `Customer`, `Product` - running the sweep after each. Then RLS with `FORCE`.

### Phase 4 - Residuals

- **Media paths:** `products/{tenant}/{YYYY}/{MM}/` and `receipts/{tenant}/{YYYY}/{MM}/` -
  type prefix first (section 4.1). **Do not migrate existing paths.** The default tenant's
  files stay where they are and still route correctly; only new uploads carry the tenant
  segment. Zero downtime, zero blob copying.
- **`purge_receipts` and `update_bcv_rate`:** loop tenants under `tenant_context`. Note that
  `update_bcv_rate` currently raises `CommandError` on a bad source URL - one tenant's
  failure must not abort the rest.
- **Admin:** `SystemSettingsAdmin.has_add_permission` (`core/admin.py:143`) currently returns
  `not SystemSettings.objects.exists()`, so under (a) once one tenant has settings no other
  can create theirs. Under (a) also add `get_queryset()` **and `formfield_for_foreignkey`**
  to all 11 `ModelAdmin`s - the latter is the admin's exact analogue of the related-field
  leak, and without it every FK dropdown lists other tenants' objects.
- **Kiosk provisioning email** (`api/kiosk/provisioning.py:44`): add the tenant slug, or the
  derived `kiosk-{store}-{n}@station.internal` collides on `User.email`.
- **`store_identifier`:** promote to a real `Store` FK, or at minimum add tenant to
  `unique_together`. Low priority - it is free text that never filters a query, so it is dead
  weight rather than a hazard.

### Phase 5 - Provisioning

`OperationalSiteInitializer.run()` (`core/management/site_initialization.py:256-292`) already
creates roles, settings, the admin user, and kiosk stations inside a single
`transaction.atomic()`. Wrapping it in a schema context yields
`manage.py provision_tenant --slug acme` in roughly thirty lines.

This is the strongest existing asset in the repository - the difference between "provisioning
is a project" and "provisioning is a command."

---

## 11. What stays hard

**Per-tenant OCR keys are free; attribution is not.** The keys already live in
`SystemSettings.ocr_api_key` rather than in environment variables, so per-tenant keys fall
out of breaking the singleton. But `OcrCallLog` becomes per-schema, so billing OCR usage
requires a nightly rollup into public. Price this in Phase 0, not at invoice time.

**The shared VEPay dependency has no clean answer.** Either a shared account, where one
tenant exhausting the quota breaks everyone, or per-tenant accounts, where you inherit N
vendor relationships and lose central debugging. Recommend per-tenant keys with a platform
fallback, plus a per-tenant quota enforced inside `VEPayClient` and driven by `OcrCallLog`.
Add a **per-tenant circuit breaker**: `core/services/vepay.py:93` retries twice at a
30-second timeout, so a degraded provider costs a full minute per request. The breaker needs
the shared cache from Phase 0.

**SMTP and sender identity.** Password-reset links already follow `request.get_host()` -
`django.contrib.sites` is not installed, so `get_current_site` falls back to `RequestSite` -
which means subdomain links work for free. A genuine asset. But `DEFAULT_FROM_EMAIL` is
global. Per-tenant sending means either per-tenant SMTP credentials (another secret, another
support surface) or one sender with a per-tenant display name and SPF/DKIM on your domain
only. The second is right at this scale, but it means tenants **cannot** send from their own
domain without DKIM delegation. That is a sales conversation, and it should be written down
before someone promises otherwise.

**Backup, restore, and deletion.** Under (b), `pg_dump -n acme` and `DROP SCHEMA acme
CASCADE`. Under (a), per-tenant restore means restoring the full database to a scratch
instance and copying rows back in FK order across 14 tables. This is where (a) is weakest and
it is almost never priced.

Media is a separate axis under every model: buckets are not tenant-partitioned unless the
*path* is. That is why the Phase 4 path change matters beyond hygiene - it is what makes
deleting one tenant's receipts a prefix operation rather than a database-driven object walk.

**Noisy neighbours are currently unaddressable.** No `CACHES` means per-process
`LocMemCache`, so the effective limit is `rate x workers` and it resets on deploy. Worse for
kiosks specifically: the kiosk throttles key on the **station**, so a tenant with 50 terminals
gets 50 times the budget of a tenant with one. A tenant-level scope must sit above the
station scope. And throttling cannot touch database contention at all - one tenant's report
scanning millions of rows needs a per-connection `statement_timeout`, set per tenant, which
is another thing the connection-setup hook must do alongside `search_path`.

**Custom domains leak into code in two non-obvious places.** `*.retailops.app` is free via the
`ALLOWED_HOSTS` wildcard. A tenant-owned domain needs the `Domain` table, per-domain TLS
automation you do not have, and: **`CSRF_TRUSTED_ORIGINS` is read from settings at import**,
so dynamic per-tenant origins need either a wildcard entry or custom CSRF middleware; and
**`KioskCORSMiddleware` builds its allowlist once in `__init__`** from one environment
variable, so per-tenant kiosk origins must become a request-scoped computation. Both fail
closed in confusing ways.

**Offboarding.** `pg_dump -n` is a *backup*, not a customer-usable *export*. You need
`dumpdata` inside a tenant context, a manifest, a media sync, and - critically - a test that
**round-trips the export into an empty tenant**. Without that round-trip test the export
format is aspirational.

**Two more, easy to forget.** Cross-tenant reporting *for you as the vendor* is permanently a
rollup job under (b) and (c). And `drf-spectacular` instantiates serializers and touches
querysets, so under (a) with a fail-closed manager `manage.py spectacular` raises until it is
wrapped - minor, but it will surprise someone in CI.

---

## 12. Client impact

### Konteo Express (kiosk) - zero code changes

A station's API key already resolves 1:1 to a `KioskStation`
(`api/kiosk/authentication.py:39-52`). Adding a tenant to that station makes every kiosk
request tenant-scoped **server-side**, with no client change. This property is worth
protecting: these are physical terminals in shops, and a configuration change means a site
visit.

Two provisioning-level items, not code:

- `BASE_URL` per station must point at the tenant's host. It is already per-station
  configuration via `config.local.js` or the native settings screen.
- The mixed-content guard rejects an `http://` `BASE_URL` when served over HTTPS, with
  loopback exempted. Correct behaviour for real tenant subdomains.

One opportunity: branding (`STORE_NAME`, `BRAND_LOGO_URL`, `KIOSK_THEME`) is client-side
configuration today, but `app/services/settings.js` already fetches `GET /settings/` on boot
and applies it with a conservative-fallback `catch`. Serving branding from the tenant's
settings row would reduce per-station configuration to the two irreducible secrets -
`BASE_URL` and `KIOSK_API_KEY` - which is exactly what SaaS onboarding wants.

### RetailOps CLI - works unchanged under subdomain tenancy

Profiles are already `(base_url, token)` pairs with a full resolution chain, so
`auth login --url https://acme.retailops.app/api/v1 --profile acme` gives one operator
multi-tenant access with no code change. **This is a real argument for making the subdomain,
rather than a header, the primary mechanism** - a header-based scheme would need a new
`Profile` field.

Two latent issues worth fixing regardless:

- `set_profile_token` writes a fixed four-key dict, so any field added to a profile is
  **silently dropped on the next `auth login`**.
- The `kiosk` command group builds its client by reusing the *active profile's* `base_url`
  with the token swapped. A kiosk key from tenant A used while profile B is active would hit
  the wrong host - a genuine cross-tenant footgun once tenants exist.

### MCP server - inherits scoping for free

`mcp_server/` performs no ORM access; it is an HTTP client of `/api/v1/`. Whatever scoping the
API gains, the MCP layer gets automatically. Its `retailops://` resource URIs may want a
tenant segment for clarity, but nothing is required for correctness.
