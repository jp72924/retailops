# RetailOps Backend — Production Manual

Follow this manual **to the letter** to take the RetailOps backend from source to
a **production-ready** deployment. Every step is mandatory unless explicitly
marked *Optional*. The final section is a **go-live gate**: do not expose the
service publicly until every box is checked.

"Production-ready" here means: secure (no debug, real secrets, HTTPS-only
cookies, hardened headers), durable (PostgreSQL + tested backups), correct under
concurrency (shared cache so rate-limits are real), observable (logs + health
checks), and recoverable (documented restore + rollback).

> Conventions: replace every `UPPERCASE_PLACEHOLDER`. Commands assume Debian/
> Ubuntu Linux and a dedicated service user `appuser` with the code at
> `/opt/retailops/app`. Adjust paths if yours differ, but keep them consistent.

---

## 0. Architecture (what you are deploying)

```
            HTTPS (443)
 Internet ───────────────▶  Reverse proxy (Caddy or nginx)  ── TLS termination,
                                   │  X-Forwarded-Proto=https     security headers,
                                   │                              serves /static, /media
                          proxy ▼ (127.0.0.1:8000, internal only)
                            gunicorn  ── Django (retailops.wsgi)
                                   │
                ┌──────────────────┼───────────────────┐
                ▼                  ▼                    ▼
          PostgreSQL          Redis cache         Object storage
        (orders, stock)    (throttling state)   (local | GCS | S3/RustFS)
```

Mandatory production components: **PostgreSQL** (not SQLite), a **TLS reverse
proxy**, **gunicorn** under **systemd**, and a **shared cache** (Redis) so
throttling is accurate across workers. Object storage is local disk or a managed/
self-hosted S3/GCS bucket.

---

## 1. Non-negotiables (production gate, summary)

The deploy is **not** production-ready unless ALL are true:

1. `DJANGO_DEBUG=False`.
2. `DJANGO_SECRET_KEY` set to a strong random value (the app refuses to start
   with the default key when `DEBUG=False`).
3. `DJANGO_ALLOWED_HOSTS` set to your real hostname(s).
4. **PostgreSQL** via `DATABASE_URL` (SQLite is single-writer — unfit for
   concurrent stations and live stock locking).
5. **HTTPS** enforced; secure cookies + HSTS on.
6. **Shared cache (Redis)** configured — otherwise multi-worker throttling
   under-counts and rate limits are effectively multiplied by the worker count.
7. `python manage.py check --deploy` returns no unaddressed warnings.
8. Backups run on a schedule **and a restore has been tested**.

---

## 2. Prerequisites and versions

- Linux server, 2 vCPU / 2 GB RAM minimum (4 GB if receipt OCR/image processing
  is used).
- Python 3.11+ (3.12 recommended). PostgreSQL 14+. Redis 6+.
- Dependencies are pinned in `requirements.txt` (Django 4.2 LTS, DRF, gunicorn,
  psycopg, django-storages, Pillow, etc.). Do not float versions in production.

Install base packages:

```bash
sudo apt-get update
sudo apt-get -y install postgresql redis-server git python3-venv python3-pip
```

---

## 3. Secrets management

Never commit secrets. Store them only in `/etc/retailops.env` (root-owned, group
-readable by `appuser`), or a secrets manager (GCP Secret Manager, Vault).

Generate the Django secret key:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(64))"
```

Required secrets: `DJANGO_SECRET_KEY`, the database password (inside
`DATABASE_URL`), object-storage keys (if S3/GCS), and the SMTP password (if
email). Rotate the secret key only during a maintenance window (invalidates
sessions/signed values).

---

## 4. Environment variable reference (authoritative)

Set these in `/etc/retailops.env`. **Required** for production unless noted.

### 4.1 Core security

| Variable | Required | Production value | Notes |
|---|---|---|---|
| `DJANGO_DEBUG` | ✅ | `False` | Must be `False`. |
| `DJANGO_SECRET_KEY` | ✅ | *(random 64-char)* | App refuses default when `DEBUG=False`. |
| `DJANGO_ALLOWED_HOSTS` | ✅ | `api.example.com` | Comma-separated. Empty + `DEBUG=False` blocks all requests. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | ✅ | `https://api.example.com` | Needed for admin login over HTTPS. Comma-separated, include scheme. |
| `DJANGO_SECURE_SSL_REDIRECT` | rec | `True` | Defaults `True` when `DEBUG=False`. Redirect HTTP→HTTPS. |
| `DJANGO_SECURE_PROXY_SSL_HEADER` | ✅ behind proxy | `True` | Defaults `True` when not debug. Trusts `X-Forwarded-Proto`; your proxy **must** send it. |
| `DJANGO_SESSION_COOKIE_SECURE` | rec | `True` | Default `True` when not debug. |
| `DJANGO_CSRF_COOKIE_SECURE` | rec | `True` | Default `True` when not debug. |
| `DJANGO_SECURE_HSTS_SECONDS` | rec | `31536000` | Set **after** confirming HTTPS works. 0 disables. |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` | rec | `True` | Only if all subdomains are HTTPS. |
| `DJANGO_SECURE_HSTS_PRELOAD` | opt | `True` | Only if you intend to submit to the HSTS preload list. |

### 4.2 Database

| Variable | Required | Example | Notes |
|---|---|---|---|
| `DATABASE_URL` | ✅ | `postgres://retailops:PASS@127.0.0.1:5432/retailops` | PostgreSQL only. Without it, falls back to SQLite (dev only). |
| `DB_CONN_MAX_AGE` | opt | `60` | Persistent connection seconds. Default 60. |

### 4.3 CORS (kiosk/PWA)

| Variable | Required | Example | Notes |
|---|---|---|---|
| `KIOSK_CORS_ORIGINS` | ✅ | `https://kiosk.example.com,capacitor://localhost` | Browser PWA origin + native app origin. In `DEBUG` only, localhost is auto-allowed; in production you must list every origin. |

### 4.4 Media / object storage

Choose ONE backend.

| Variable | When | Notes |
|---|---|---|
| `MEDIA_STORAGE_BACKEND` | ✅ | `local`, `gcs`, or `s3`. |
| `MEDIA_ROOT` | local | Absolute path, e.g. `/opt/retailops/app/media`. |
| `MEDIA_GCS_BUCKET_NAME` (or `GS_BUCKET_NAME`) | gcs | Bucket name; service-account creds via `GOOGLE_APPLICATION_CREDENTIALS` or workload identity. |
| `MEDIA_S3_ENDPOINT_URL` | s3 | e.g. `https://s3.example.com` (or RustFS/MinIO endpoint). |
| `MEDIA_S3_BUCKET_NAME` *(or `MEDIA_S3_PRODUCT_BUCKET_NAME` + `MEDIA_S3_RECEIPT_BUCKET_NAME`)* | s3 | Buckets. |
| `MEDIA_S3_ACCESS_KEY_ID`, `MEDIA_S3_SECRET_ACCESS_KEY` | s3 | Credentials. |
| `MEDIA_S3_REGION_NAME` | s3 opt | Default `us-east-1`. |
| `MEDIA_S3_ADDRESSING_STYLE` | s3 opt | `path` for MinIO/RustFS. |
| `MEDIA_S3_PRODUCT_PUBLIC` | s3 opt | `True` = product images public. |
| `MEDIA_S3_RECEIPT_SIGNED_URLS` | s3 opt | `True` = receipts private/signed. Keep `True`. |

Static files are always served from `STATIC_ROOT` (`<app>/staticfiles`) via the
reverse proxy after `collectstatic`.

### 4.5 Email (password reset, notifications) — Optional but recommended

| Variable | Default | Notes |
|---|---|---|
| `DJANGO_EMAIL_BACKEND` | console | Set to SMTP backend for real mail. |
| `DJANGO_EMAIL_HOST` / `DJANGO_EMAIL_PORT` | localhost / 587 | SMTP server. |
| `DJANGO_EMAIL_USE_TLS` | `True` | |
| `DJANGO_EMAIL_HOST_USER` / `DJANGO_EMAIL_HOST_PASSWORD` | empty | SMTP creds. |
| `DJANGO_DEFAULT_FROM_EMAIL` | `noreply@retailops.local` | From address. |

### 4.6 Example `/etc/retailops.env` (production)

```bash
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=REPLACE_WITH_64_CHAR_RANDOM
DJANGO_ALLOWED_HOSTS=api.example.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://api.example.com
DJANGO_SECURE_HSTS_SECONDS=31536000
DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=True

DATABASE_URL=postgres://retailops:DB_PASSWORD@127.0.0.1:5432/retailops
DB_CONN_MAX_AGE=60

KIOSK_CORS_ORIGINS=https://kiosk.example.com,capacitor://localhost

MEDIA_STORAGE_BACKEND=local
MEDIA_ROOT=/opt/retailops/app/media

DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
DJANGO_EMAIL_HOST=smtp.example.com
DJANGO_EMAIL_PORT=587
DJANGO_EMAIL_USE_TLS=True
DJANGO_EMAIL_HOST_USER=SMTP_USER
DJANGO_EMAIL_HOST_PASSWORD=SMTP_PASS
DJANGO_DEFAULT_FROM_EMAIL=noreply@example.com

# Set after Redis is configured (Section 7):
REDIS_URL=redis://127.0.0.1:6379/0
```

Lock it down:

```bash
sudo chmod 640 /etc/retailops.env
sudo chown root:appuser /etc/retailops.env
```

---

## 5. Database setup

```bash
sudo -u postgres psql -c "CREATE USER retailops WITH PASSWORD 'DB_PASSWORD';"
sudo -u postgres psql -c "CREATE DATABASE retailops OWNER retailops;"
```

Deploy the schema and the operational minimum (roles, settings, first admin).
Run as `appuser` with the env loaded:

```bash
sudo -u appuser bash -lc '
set -a; source /etc/retailops.env; set +a
cd /opt/retailops/app
./.venv/bin/python manage.py migrate
./.venv/bin/python manage.py init --no-input --yes --admin-email OWNER_EMAIL
'
```

> `init` creates roles, system settings, and the first admin only — no sample
> data. Use `--demo --seed` **only** on a staging box, never production.

Concurrency note: the atomic checkout uses row-level locking
(`select_for_update`) in PostgreSQL — this is why SQLite is disallowed in
production.

---

## 6. Static and media

```bash
sudo -u appuser bash -lc '
set -a; source /etc/retailops.env; set +a
cd /opt/retailops/app
./.venv/bin/python manage.py collectstatic --noinput
'
```

- **Static** → served by the reverse proxy from `<app>/staticfiles`.
- **Media** → local disk (`MEDIA_ROOT`) served by the proxy, or an object store
  (`gcs`/`s3`). For S3/RustFS, see `DEPLOY_GCP_VM.md` Appendix B for endpoint +
  proxy routing.

---

## 7. Shared cache + accurate throttling (REQUIRED for multi-worker)

The API enforces rate limits (login `20/min`, `kiosk_checkout 30/min`,
`kiosk_scan 120/min`, global `user 600/min`, etc.). DRF stores throttle counters
in Django's cache. The default cache is **per-process in memory** — with N
gunicorn workers the real limit becomes ~N× the configured value and is not
shared. **For correct limits you must use a shared cache (Redis).**

Redis is installed (Section 2). Confirm it runs:

```bash
sudo systemctl enable --now redis-server
redis-cli ping     # -> PONG
```

The shipped `settings.py` does not read a cache URL by default, so add a cache
configuration. Create `retailops/production_overrides` via a small, committed
settings addition **or** append to settings using an env-driven block. Minimal
addition (put in `retailops/settings.py`, or a `local_settings.py` imported at
the end of settings):

```python
# Shared cache for throttling/session-independent state (production).
import os
_redis_url = os.environ.get("REDIS_URL")
if _redis_url:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _redis_url,
        }
    }
```

Set `REDIS_URL=redis://127.0.0.1:6379/0` in `/etc/retailops.env` (already in the
example). Restart the app after this change.

> If you cannot add a shared cache, the only correct alternative is to run
> **gunicorn with a single worker** (`--workers 1`), which limits throughput.
> Multi-worker without shared cache = broken rate limiting. Do not ship that.

Throttle rates themselves are defined in `settings.py` (`DEFAULT_THROTTLE_RATES`)
— change them there, not via env.

---

## 8. Application server (gunicorn + systemd)

Worker count: `(2 × CPU_CORES) + 1`. Bind to localhost only.

```bash
sudo tee /etc/systemd/system/retailops.service >/dev/null <<'EOF'
[Unit]
Description=RetailOps Django (gunicorn)
After=network.target postgresql.service redis-server.service
Wants=postgresql.service redis-server.service

[Service]
User=appuser
Group=appuser
WorkingDirectory=/opt/retailops/app
EnvironmentFile=/etc/retailops.env
ExecStart=/opt/retailops/app/.venv/bin/gunicorn retailops.wsgi:application \
  --bind 127.0.0.1:8000 --workers 3 --timeout 60 \
  --access-logfile - --error-logfile -
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now retailops
sudo systemctl status retailops --no-pager
```

gunicorn listens only on `127.0.0.1:8000`. Never expose it directly.

---

## 9. Reverse proxy + TLS

Terminate TLS at the proxy and forward `X-Forwarded-Proto`. Two supported
options:

- **Caddy** — automatic Let's Encrypt; see `DEPLOY_GCP_VM.md` Part 11 for a
  ready Caddyfile (serves `/static`, `/media`, proxies the rest). Caddy sets
  `X-Forwarded-Proto` automatically.
- **nginx** — terminate TLS (certbot), `proxy_pass http://127.0.0.1:8000;`, and
  set `proxy_set_header X-Forwarded-Proto $scheme;` plus `Host $host;`.

After HTTPS is confirmed working end-to-end, enable HSTS (Section 4.1). Enabling
HSTS before HTTPS is solid can lock clients out — verify first.

---

## 10. Security verification

Run Django's deploy auditor with the production env loaded:

```bash
sudo -u appuser bash -lc '
set -a; source /etc/retailops.env; set +a
cd /opt/retailops/app
./.venv/bin/python manage.py check --deploy
'
```

Resolve every warning, or consciously accept it with a written reason. Expected
green items when configured per this manual: DEBUG off, secret key set, secure
cookies, HSTS, SSL redirect, allowed hosts.

Additional hardening:
- Firewall: expose only **80/443** (proxy) and **22** (SSH, restricted to admin
  IPs). Keep PostgreSQL (5432), Redis (6379), gunicorn (8000) bound to
  localhost only — never public.
- Restrict the OpenAPI docs in production if you do not want them public:
  `/api/v1/schema/swagger/` and `/redoc/` reveal the full API surface.
- Keep OS + Python deps patched; rebuild from pinned `requirements.txt`.

---

## 11. Logging and monitoring

- App logs go to stdout/stderr → captured by journald:
  `sudo journalctl -u retailops -f`. Ship to a log service if available.
- **Liveness/health check:** `POST /api/v1/kiosk/heartbeat/` with a valid
  `Authorization: KioskKey <key>` returns `200` — use it for uptime monitoring.
  For a no-auth check, monitor that `GET /api/v1/schema/` (or `/admin/login/`)
  returns `200/302` over HTTPS.
- Alert on: process down (systemd restart loops), 5xx rate, DB connection
  errors, disk > 80%, cert expiry (Caddy auto-renews; still alert).

---

## 12. Backups and restore (must test)

A backup you have never restored is not a backup.

Nightly: dump DB + sync media off-box (object store or another host).

```bash
sudo tee /opt/retailops/backup.sh >/dev/null <<'EOF'
#!/bin/bash
set -euo pipefail
STAMP=$(date +%Y%m%d-%H%M%S)
DEST="BACKUP_DESTINATION"     # e.g. gs://retailops-backups-... or /mnt/backups
sudo -u postgres pg_dump retailops | gzip > "/tmp/db-$STAMP.sql.gz"
gcloud storage cp "/tmp/db-$STAMP.sql.gz" "$DEST/" || cp "/tmp/db-$STAMP.sql.gz" "$DEST/"
# media: only if MEDIA_STORAGE_BACKEND=local
tar -czf "/tmp/media-$STAMP.tar.gz" -C /opt/retailops/app media 2>/dev/null || true
gcloud storage cp "/tmp/media-$STAMP.tar.gz" "$DEST/" 2>/dev/null || true
rm -f /tmp/db-$STAMP.sql.gz /tmp/media-$STAMP.tar.gz
EOF
sudo chmod +x /opt/retailops/backup.sh
echo "0 3 * * * /opt/retailops/backup.sh >> /var/log/retailops-backup.log 2>&1" | sudo tee /etc/cron.d/retailops-backup
sudo chmod 644 /etc/cron.d/retailops-backup
```

**Restore drill (run once now, on a staging DB):**

```bash
gunzip -c db-STAMP.sql.gz | sudo -u postgres psql retailops_restore_test
```

For stronger guarantees, enable PostgreSQL point-in-time recovery (WAL archiving)
or use a managed database with automated backups + PITR.

---

## 13. Upgrades and rollback

Deploy a new version:

```bash
sudo -u appuser bash -lc '
set -a; source /etc/retailops.env; set +a
cd /opt/retailops/app
git fetch --all
git checkout TARGET_TAG_OR_COMMIT
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python manage.py migrate
./.venv/bin/python manage.py collectstatic --noinput
'
sudo systemctl restart retailops
```

- **Always back up the DB immediately before `migrate`** (migrations can be
  hard to reverse).
- **Rollback:** `git checkout PREVIOUS_TAG`, reinstall deps, restart. If a
  migration must be undone, restore the pre-migration DB dump.
- Pin to tags/commits, never deploy a moving branch in production.

---

## 14. Provision kiosk stations

Each physical station needs its own key (terminal on 401 — no shared keys):

```bash
sudo -u appuser bash -lc '
set -a; source /etc/retailops.env; set +a
cd /opt/retailops/app
./.venv/bin/python manage.py provision_kiosk --store STORE_ID --station N --by ADMIN_EMAIL
'
```

Record the printed key (shown once) into the station/app. Deactivate lost
stations server-side to invalidate their key.

---

## 15. Go-live gate (do not skip)

Tick every box before exposing the service:

- [ ] `DJANGO_DEBUG=False`; app starts (proves real `DJANGO_SECRET_KEY`).
- [ ] `DJANGO_ALLOWED_HOSTS` + `DJANGO_CSRF_TRUSTED_ORIGINS` set to real host.
- [ ] `DATABASE_URL` points at **PostgreSQL**; `migrate` clean; `init` done.
- [ ] Redis up; `REDIS_URL` set; `CACHES` configured → throttling shared.
- [ ] gunicorn under systemd, `enabled` (starts on boot), localhost-only bind.
- [ ] Reverse proxy serves HTTPS; `X-Forwarded-Proto` forwarded.
- [ ] Secure cookies + SSL redirect on; HSTS enabled after HTTPS verified.
- [ ] `manage.py check --deploy` — no unaddressed warnings.
- [ ] Firewall: only 80/443/22 public; 5432/6379/8000 localhost only.
- [ ] `collectstatic` done; static + media load over HTTPS.
- [ ] `KIOSK_CORS_ORIGINS` lists the real PWA + `capacitor://localhost`.
- [ ] Heartbeat returns 200 over HTTPS with a real station key.
- [ ] Nightly backup scheduled **and a restore was tested**.
- [ ] Monitoring/alerts live (process, 5xx, disk, cert).
- [ ] Admin login works with secure cookies; OpenAPI docs restricted if desired.

When all are checked, the backend is production-ready.

---

## 16. Operational quick reference

| Action | Command |
|---|---|
| Restart app | `sudo systemctl restart retailops` |
| App logs (live) | `sudo journalctl -u retailops -f` |
| Deploy audit | `manage.py check --deploy` (env loaded) |
| New station key | `manage.py provision_kiosk --store S --station N --by EMAIL` |
| Run backup now | `sudo /opt/retailops/backup.sh` |
| DB shell | `sudo -u postgres psql retailops` |
| Redis check | `redis-cli ping` |
