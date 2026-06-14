#!/bin/bash
# RetailOps unattended production-ish setup (runs as root on first boot).
set -euxo pipefail
exec > /var/log/retailops-setup.log 2>&1

NIP="34-23-212-179.nip.io"
ADMIN_EMAIL="owner@example.com"
APPDIR="/opt/retailops/app"
PROJECT="$(curl -s -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/project/project-id)"
BUCKET="gs://retailops-backups-${PROJECT}"

# Generated secrets
DB_PASSWORD="$(openssl rand -hex 24)"
ADMIN_PASSWORD="$(openssl rand -base64 18)"
SECRET_KEY="$(python3 - <<'PY'
import secrets; print(secrets.token_urlsafe(64))
PY
)"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y upgrade
apt-get -y install postgresql redis-server git python3-venv python3-pip curl gnupg openssl debian-keyring debian-archive-keyring apt-transport-https

# Caddy (HTTPS reverse proxy)
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
apt-get update
apt-get -y install caddy

systemctl enable --now redis-server postgresql

# Database
sudo -u postgres psql -c "CREATE USER retailops WITH PASSWORD '${DB_PASSWORD}';"
sudo -u postgres psql -c "CREATE DATABASE retailops OWNER retailops;"

# App user + code
id appuser >/dev/null 2>&1 || useradd --system --create-home --home-dir /opt/retailops --shell /usr/sbin/nologin appuser
git clone https://github.com/jp72924/retailops.git "$APPDIR"
chown -R appuser:appuser /opt/retailops

# Python env
sudo -u appuser bash -lc "
cd $APPDIR
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
./.venv/bin/pip install gunicorn redis
"

# Shared cache block for accurate throttling (idempotent append)
if ! grep -q 'RETAILOPS_PROD_CACHE' "$APPDIR/retailops/settings.py"; then
cat >> "$APPDIR/retailops/settings.py" <<'PY'

# RETAILOPS_PROD_CACHE: shared Redis cache so DRF throttling is correct across workers.
import os as _os
_redis_url = _os.environ.get("REDIS_URL")
if _redis_url:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _redis_url,
        }
    }
PY
fi

# Environment file
cat > /etc/retailops.env <<EOF
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=${SECRET_KEY}
DJANGO_ALLOWED_HOSTS=${NIP},127.0.0.1,localhost
DJANGO_CSRF_TRUSTED_ORIGINS=https://${NIP}
DATABASE_URL=postgres://retailops:${DB_PASSWORD}@127.0.0.1:5432/retailops
DB_CONN_MAX_AGE=60
REDIS_URL=redis://127.0.0.1:6379/0
MEDIA_STORAGE_BACKEND=local
MEDIA_ROOT=${APPDIR}/media
KIOSK_CORS_ORIGINS=https://${NIP},capacitor://localhost
EOF
chmod 640 /etc/retailops.env
chown root:appuser /etc/retailops.env

# Migrate, static, first admin, kiosk station
sudo -u appuser bash -lc "
set -a; source /etc/retailops.env; set +a
export RETAILOPS_INITIAL_ADMIN_PASSWORD='${ADMIN_PASSWORD}'
cd $APPDIR
./.venv/bin/python manage.py migrate
./.venv/bin/python manage.py collectstatic --noinput
./.venv/bin/python manage.py init --no-input --yes --admin-email ${ADMIN_EMAIL}
./.venv/bin/python manage.py provision_kiosk --store MAIN --station 1 --by ${ADMIN_EMAIL}
" | tee /root/retailops-init.log

# Extract the station key printed by provision_kiosk
STATION_KEY="$(grep -A2 'API KEY' /root/retailops-init.log | grep -Eo '[A-Za-z0-9_-]{40,}' | head -n1 || true)"

# gunicorn service
cat > /etc/systemd/system/retailops.service <<'EOF'
[Unit]
Description=RetailOps Django (gunicorn)
After=network.target postgresql.service redis-server.service
Wants=postgresql.service redis-server.service

[Service]
User=appuser
Group=appuser
WorkingDirectory=/opt/retailops/app
EnvironmentFile=/etc/retailops.env
ExecStart=/opt/retailops/app/.venv/bin/gunicorn retailops.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 60 --access-logfile - --error-logfile -
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now retailops

# Caddy reverse proxy + auto HTTPS
chmod o+rx /opt/retailops /opt/retailops/app
chmod -R o+rX /opt/retailops/app/staticfiles /opt/retailops/app/media 2>/dev/null || true
cat > /etc/caddy/Caddyfile <<EOF
${NIP} {
    encode gzip
    handle_path /static/* {
        root * ${APPDIR}/staticfiles
        file_server
    }
    handle_path /media/* {
        root * ${APPDIR}/media
        file_server
    }
    reverse_proxy 127.0.0.1:8000
}
EOF
systemctl restart caddy

# Backups: bucket + nightly cron
gcloud storage buckets create "$BUCKET" --location=US 2>/dev/null || true
cat > /opt/retailops/backup.sh <<EOF
#!/bin/bash
set -euo pipefail
STAMP=\$(date +%Y%m%d-%H%M%S)
sudo -u postgres pg_dump retailops | gzip > "/tmp/db-\$STAMP.sql.gz"
tar -czf "/tmp/media-\$STAMP.tar.gz" -C ${APPDIR} media 2>/dev/null || true
gcloud storage cp /tmp/db-\$STAMP.sql.gz /tmp/media-\$STAMP.tar.gz "$BUCKET/" || true
rm -f /tmp/db-\$STAMP.sql.gz /tmp/media-\$STAMP.tar.gz
EOF
chmod +x /opt/retailops/backup.sh
echo "0 3 * * * root /opt/retailops/backup.sh >> /var/log/retailops-backup.log 2>&1" > /etc/cron.d/retailops-backup
chmod 644 /etc/cron.d/retailops-backup

# Credentials summary for retrieval
cat > /root/retailops-credentials.txt <<EOF
RetailOps deployment ready.
URL:            https://${NIP}
Admin email:    ${ADMIN_EMAIL}
Admin password: ${ADMIN_PASSWORD}
Station key:    ${STATION_KEY}
DB password:    ${DB_PASSWORD}
Backup bucket:  ${BUCKET}
EOF
chmod 600 /root/retailops-credentials.txt

echo "SETUP COMPLETE"
