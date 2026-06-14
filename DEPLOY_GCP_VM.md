# Deploy RetailOps Backend on a Google Cloud VM (beginner guide)

This guide takes you from **zero** to a **live, HTTPS backend** that the Konteo
Express / RetailOps Kiosk app can connect to, running everything on **one small
Google Cloud virtual machine (VM)**.

No prior server experience needed. You will copy-paste commands. Every command
is explained in plain language. Anything you must change yourself is written in
`UPPERCASE_LIKE_THIS` — replace it (including the angle brackets if any).

**What you will end up with**
- One Linux VM running: the PostgreSQL database, the Django backend (via
  gunicorn), and the Caddy web server (which gives you free HTTPS).
- A public address like `https://34-12-56-78.nip.io` that any phone can reach.
- Automatic nightly backups of the database and uploaded images to Google Cloud
  Storage.

**Rough cost:** ~US$0–15/month (free `e2-micro` tier, or `e2-small` ~US$13/mo
plus a static IP and a few cents of backup storage).

**Time:** ~45–60 minutes the first time.

---

## What is "the nip.io path"?

Phones require **HTTPS** (secure `https://`). HTTPS normally needs a **domain
name**. You don't have one. `nip.io` is a free public service: the address
`34-12-56-78.nip.io` automatically points at the IP `34.12.56.78`. So you get a
real domain name for free, and the Caddy web server can fetch a real, trusted
HTTPS certificate for it automatically. No domain purchase, no DNS setup.

---

## Glossary (read once)

| Term | Plain meaning |
|---|---|
| **VM** | A rented computer in Google's data center you control over the internet. |
| **SSH** | A secure remote terminal — you type commands on the VM from your browser. |
| **PostgreSQL** | The database that stores customers, products, orders. |
| **gunicorn** | The program that runs the Django app and answers web requests. |
| **Caddy** | The web server in front; handles HTTPS and forwards traffic to gunicorn. |
| **systemd** | Linux's "keep this running and restart it on crash/reboot" manager. |
| **cron** | Linux's task scheduler (used here for nightly backups). |
| **Cloud Storage / bucket** | Google's durable file storage, used here only for backups. |

---

## Before you start (prerequisites)

1. A **Google account**.
2. A **credit/debit card** to enable billing (required even for free tier;
   you won't be charged on free tier, but Google verifies a card).
3. The backend code is public: `https://github.com/jp72924/retailops`.

> ⚠️ **Billing safety:** leaving a VM running costs money. The free `e2-micro`
> is $0, but a static IP and any larger machine cost a few dollars. The last
> section shows how to **stop** or **delete** everything to avoid charges.

---

## Part 1 — Create a project and turn on billing

1. Open <https://console.cloud.google.com> and sign in.
2. Top bar, click the **project dropdown** → **New Project**.
   - Name: `retailops` → **Create**. Wait ~20 seconds, then select it.
3. Left menu (☰) → **Billing**. If it says no billing account, click
   **Link a billing account** → **Create account** → enter your card → finish.
4. Left menu → **APIs & Services** → **Enable APIs**. Search **Compute Engine
   API** → **Enable**. (Takes ~1 minute. This lets you create VMs.)

---

## Part 2 — Create the VM

1. Left menu (☰) → **Compute Engine** → **VM instances** → **Create instance**.
2. Fill in:
   - **Name:** `retailops-vm`
   - **Region:** pick one close to your stores. For the **free tier**, choose
     `us-west1`, `us-central1`, or `us-east1`. **Zone:** leave default.
   - **Machine configuration:** series **E2**. Machine type:
     - Free tier: **`e2-micro`** (cheapest, but only 1 GB RAM — add swap later).
     - Recommended for smoothness: **`e2-small`** (2 GB RAM, ~US$13/mo).
   - **Boot disk:** click **Change** → Operating system **Debian**, version
     **Debian GNU/Linux 12 (bookworm)**, size **30 GB** → **Select**.
   - **Firewall:** check **Allow HTTP traffic** and **Allow HTTPS traffic**.
3. Click **Create**. Wait until the green check appears (~30 seconds).

You now have a running Linux computer in the cloud.

---

## Part 3 — Give the VM a permanent IP address (static IP)

By default the IP changes if the VM restarts, which would break `nip.io`. Pin it.

1. Left menu → **VPC network** → **IP addresses** → **External IP addresses**.
2. Find the row for `retailops-vm`. In the **Type** column it says *Ephemeral*.
   Click it and change to **Static** → give it a name `retailops-ip` →
   **Reserve**.
3. **Write down the IP address** shown, e.g. `34.12.56.78`.

**Build your nip.io hostname now:** take the IP, replace each dot `.` with a
dash `-`, then add `.nip.io`.
Example: `34.12.56.78` → **`34-12-56-78.nip.io`**.

Write this hostname down — you'll use it several times. In this guide it appears
as `HOST_NIPIO` (e.g. `34-12-56-78.nip.io`).

---

## Part 4 — Connect to the VM (browser SSH)

1. Back to **Compute Engine → VM instances**.
2. On the `retailops-vm` row, click the **SSH** button. A black terminal window
   opens in your browser. **This is the VM's command line.** You type here.

> Tip: if the SSH window closes or hangs, just click **SSH** again to reopen.

From here on, **paste each command and press Enter**. Wait for it to finish
before the next. Lines starting with `#` are comments — you can paste them too,
they do nothing.

---

## Part 5 — Update the system and install software

Paste this whole block:

```bash
# Become administrator for the install steps
sudo apt-get update
sudo apt-get -y upgrade

# Core tools: database, Python, git, web server prerequisites
sudo apt-get -y install postgresql git python3-venv python3-pip curl debian-keyring debian-archive-keyring apt-transport-https
```

Now install **Caddy** (the HTTPS web server) from its official source:

```bash
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update
sudo apt-get -y install caddy
```

### (Only if you chose `e2-micro` / 1 GB RAM) add swap so it won't run out of memory

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## Part 6 — Create the database

PostgreSQL is installed and running. Create a database and a database user.

> **Pick a strong database password** and use it everywhere below as
> `DB_PASSWORD`. Avoid spaces and the characters `@ : / #` (they confuse the
> connection string).

```bash
sudo -u postgres psql -c "CREATE USER retailops WITH PASSWORD 'DB_PASSWORD';"
sudo -u postgres psql -c "CREATE DATABASE retailops OWNER retailops;"
```

---

## Part 7 — Create the app user and download the code

We run the app under a dedicated, limited user `appuser` (safer than running as
admin).

```bash
sudo useradd --system --create-home --home-dir /opt/retailops --shell /usr/sbin/nologin appuser
sudo git clone https://github.com/jp72924/retailops.git /opt/retailops/app
sudo chown -R appuser:appuser /opt/retailops
```

Create the Python environment and install dependencies (run as `appuser`):

```bash
sudo -u appuser bash -lc '
cd /opt/retailops/app
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
./.venv/bin/pip install gunicorn
'
```

---

## Part 8 — Create the configuration file (environment variables)

The backend reads its settings from environment variables. We store them in one
file: `/etc/retailops.env`.

First, generate a random secret key (copy the long line it prints):

```bash
/opt/retailops/app/.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(50))"
```

Now create the config file. **Replace** `HOST_NIPIO`, `DB_PASSWORD`, and
`PASTE_SECRET_KEY_HERE` before pasting:

```bash
sudo tee /etc/retailops.env >/dev/null <<'EOF'
DJANGO_DEBUG=False
DJANGO_SECRET_KEY=PASTE_SECRET_KEY_HERE
DJANGO_ALLOWED_HOSTS=HOST_NIPIO,127.0.0.1,localhost
DJANGO_CSRF_TRUSTED_ORIGINS=https://HOST_NIPIO
DATABASE_URL=postgres://retailops:DB_PASSWORD@127.0.0.1:5432/retailops
MEDIA_STORAGE_BACKEND=local
MEDIA_ROOT=/opt/retailops/app/media
KIOSK_CORS_ORIGINS=https://HOST_NIPIO,capacitor://localhost
EOF
sudo chmod 640 /etc/retailops.env
sudo chown root:appuser /etc/retailops.env
```

> What these mean: `DJANGO_DEBUG=False` = production mode (secure).
> `DJANGO_ALLOWED_HOSTS` = which addresses may reach the site. `DATABASE_URL` =
> how Django finds Postgres. `KIOSK_CORS_ORIGINS` = which app origins may call
> the API (`capacitor://localhost` is the native Android/iOS app; the `https`
> one is for the browser PWA).

---

## Part 9 — Set up the database tables and the first admin

Run the backend's setup commands. The `--demo --seed` flags load sample products
so you can test immediately (omit them for an empty real store).

```bash
sudo -u appuser bash -lc '
set -a; source /etc/retailops.env; set +a
cd /opt/retailops/app
./.venv/bin/python manage.py migrate
./.venv/bin/python manage.py collectstatic --noinput
./.venv/bin/python manage.py init --no-input --yes --admin-email owner@example.com --demo --seed
'
```

> If `init` asks for a password or you want a known one, set it first by adding
> `RETAILOPS_INITIAL_ADMIN_PASSWORD=YourStrongPass123!` on the line before
> `cd /opt/retailops/app`.

**Create a kiosk station key** (the app needs this). Copy the key it prints —
it is shown **only once**:

```bash
sudo -u appuser bash -lc '
set -a; source /etc/retailops.env; set +a
cd /opt/retailops/app
./.venv/bin/python manage.py provision_kiosk --store MAIN --station 1 --by owner@example.com
'
```

Save the printed **API KEY** somewhere safe. You'll type it into the app later.

---

## Part 10 — Keep the app running with systemd

This makes gunicorn (the app) start on boot and restart if it crashes.

```bash
sudo tee /etc/systemd/system/retailops.service >/dev/null <<'EOF'
[Unit]
Description=RetailOps Django (gunicorn)
After=network.target postgresql.service
Wants=postgresql.service

[Service]
User=appuser
Group=appuser
WorkingDirectory=/opt/retailops/app
EnvironmentFile=/etc/retailops.env
ExecStart=/opt/retailops/app/.venv/bin/gunicorn retailops.wsgi:application --bind 127.0.0.1:8000 --workers 3 --timeout 60
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now retailops
sudo systemctl status retailops --no-pager
```

The last command should show **active (running)** in green. Press `q` to exit if
it pauses.

> gunicorn listens only on `127.0.0.1:8000` (the VM's inside), not the internet.
> Caddy (next step) is the only thing exposed publicly. That's intentional and
> safer.

---

## Part 11 — Configure Caddy for HTTPS (the nip.io magic)

Caddy will: get a free HTTPS certificate for your `nip.io` host, serve the
images and static files directly (fast), and forward everything else to gunicorn.

Allow the Caddy user to read the app's static and media folders:

```bash
sudo chmod o+rx /opt/retailops /opt/retailops/app
sudo chmod -R o+rX /opt/retailops/app/staticfiles /opt/retailops/app/media 2>/dev/null || true
```

Write the Caddy config. **Replace `HOST_NIPIO`** with your value:

```bash
sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
HOST_NIPIO {
    encode gzip

    handle_path /static/* {
        root * /opt/retailops/app/staticfiles
        file_server
    }

    handle_path /media/* {
        root * /opt/retailops/app/media
        file_server
    }

    reverse_proxy 127.0.0.1:8000
}
EOF

sudo systemctl restart caddy
sudo systemctl status caddy --no-pager
```

Caddy now contacts Let's Encrypt and installs a real certificate automatically
(takes 10–30 seconds the first time). If status shows **active (running)**,
you're done with the server.

---

## Part 12 — Test it

On your own computer, open a browser to:

```
https://HOST_NIPIO/admin/
```

You should see a **secure padlock** and the Django admin login. Log in with
`owner@example.com` (or the demo admin shown by `init`, e.g.
`admin@retailops.local` / `AdminPassword123!`).

Test the kiosk API responds (paste your real key):

```
https://HOST_NIPIO/api/v1/  (should load without a browser security warning)
```

---

## Part 13 — Point the app at your backend

In the Konteo Express / RetailOps Kiosk app, on the **"Configurar estación"**
provisioning screen, enter:

- **URL del backend:** `https://HOST_NIPIO`
- **Clave de estación:** the API key from Part 9.

Tap **Guardar y reiniciar**. The app should reach the home screen.

> Because the backend is now real HTTPS, you do **not** need the emulator
> cleartext workaround (`androidScheme: http`, network-security-config). Use the
> normal production build (`androidScheme: https`).

---

## Part 14 — Automatic nightly backups to Cloud Storage

One VM can fail. These backups copy the database and uploaded images to Google
Cloud Storage every night, so you can recover.

**Create a storage bucket** (names are globally unique — change `YOURNAME`):

```bash
gcloud storage buckets create gs://retailops-backups-YOURNAME --location=US
```

**Create the backup script:**

```bash
sudo tee /opt/retailops/backup.sh >/dev/null <<'EOF'
#!/bin/bash
set -euo pipefail
STAMP=$(date +%Y%m%d-%H%M%S)
BUCKET="gs://retailops-backups-YOURNAME"
TMP=$(mktemp -d)

# Database dump
sudo -u postgres pg_dump retailops | gzip > "$TMP/db-$STAMP.sql.gz"

# Uploaded images
tar -czf "$TMP/media-$STAMP.tar.gz" -C /opt/retailops/app media 2>/dev/null || true

# Upload both, then clean up
gcloud storage cp "$TMP"/*.gz "$BUCKET/"
rm -rf "$TMP"
EOF

sudo chmod +x /opt/retailops/backup.sh
```

> Replace `YOURNAME` in **both** the bucket creation command and the script
> (the `BUCKET=` line) with the same value.

**Run it once to confirm it works:**

```bash
sudo /opt/retailops/backup.sh
gcloud storage ls gs://retailops-backups-YOURNAME/
```

You should see two `.gz` files listed.

**Schedule it nightly at 03:00** using cron:

```bash
echo "0 3 * * * /opt/retailops/backup.sh >> /var/log/retailops-backup.log 2>&1" | sudo tee /etc/cron.d/retailops-backup
sudo chmod 644 /etc/cron.d/retailops-backup
```

> The VM's built-in Google service account usually already has permission to
> write to buckets in the same project. If the test upload says *permission
> denied*, go to **IAM & Admin → IAM**, find the
> `...-compute@developer.gserviceaccount.com` account, and add the role
> **Storage Object Admin**.

---

## Part 15 — Updating the backend later

When the code changes:

```bash
sudo -u appuser bash -lc '
set -a; source /etc/retailops.env; set +a
cd /opt/retailops/app
git pull
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python manage.py migrate
./.venv/bin/python manage.py collectstatic --noinput
'
sudo systemctl restart retailops
```

---

## Troubleshooting

**See the app's logs** (most useful):
```bash
sudo journalctl -u retailops -n 50 --no-pager
```
**See Caddy / HTTPS logs:**
```bash
sudo journalctl -u caddy -n 50 --no-pager
```

| Symptom | Likely cause / fix |
|---|---|
| Browser shows **400 Bad Request** | `HOST_NIPIO` not in `DJANGO_ALLOWED_HOSTS`. Fix `/etc/retailops.env`, then `sudo systemctl restart retailops`. |
| **502 Bad Gateway** | gunicorn not running. `sudo systemctl status retailops`; read `journalctl -u retailops`. |
| HTTPS certificate fails | Ports 80/443 not open, or IP/hostname mismatch. Confirm **Allow HTTP/HTTPS** is checked on the VM, and the IP in `HOST_NIPIO` matches the **static** IP exactly. |
| App says "Estación desactivada" | Wrong/disabled kiosk key. Re-run `provision_kiosk` and use the new key. |
| Images don't load | Permissions. Re-run the `chmod o+rx` commands in Part 11. |
| Admin login "CSRF" error | `DJANGO_CSRF_TRUSTED_ORIGINS=https://HOST_NIPIO` missing. Fix env, restart. |

---

## Security checklist

- `DJANGO_DEBUG=False` (set — keep it).
- Strong, unique `DJANGO_SECRET_KEY` and `DB_PASSWORD`.
- Only ports **80, 443** (web) and **22** (SSH) are open. Never expose 5432
  (Postgres) or 8000 (gunicorn) to the internet — this guide already keeps them
  internal.
- Restrict SSH to your own IP: **VPC network → Firewall → default-allow-ssh →
  Edit → Source IPv4 ranges →** your home IP `/32`.
- Keep the OS patched: `sudo apt-get update && sudo apt-get -y upgrade` monthly,
  or install `unattended-upgrades`.
- Back up off the VM (Part 14) — done.

---

## Cost control — stop or delete to avoid charges

- **Stop** (keeps data, stops most compute charges; static IP still bills a few
  cents/day while the VM is stopped):
  **Compute Engine → VM instances → ⋮ → Stop**.
- **Start again:** same menu → **Start**. (The static IP and `nip.io` host stay
  the same — that's why we reserved it.)
- **Delete everything** (no more charges): delete the **VM**, then **VPC
  network → IP addresses → release** the static IP, and optionally delete the
  backup **bucket**. Note: deleting is permanent.

---

## Quick reference (after setup)

| Action | Command |
|---|---|
| Restart app | `sudo systemctl restart retailops` |
| Restart web server | `sudo systemctl restart caddy` |
| App logs | `sudo journalctl -u retailops -f` |
| New station key | `sudo -u appuser bash -lc 'set -a; source /etc/retailops.env; set +a; cd /opt/retailops/app; ./.venv/bin/python manage.py provision_kiosk --store MAIN --station 2 --by owner@example.com'` |
| Run backup now | `sudo /opt/retailops/backup.sh` |

Your backend address: **`https://HOST_NIPIO`**


---

# Appendix A — Provision the GCP infrastructure with commands (gcloud / Terraform)

Parts 1–4 used the web console (point-and-click). This appendix does the **same
thing with commands** — faster, repeatable, and easy to tear down. Two options:

- **A.1 `gcloud` CLI** — imperative scripts. Good for one-off setup.
- **A.2 Terraform** — declarative "infrastructure as code". **Recommended** when
  you want the setup version-controlled, reproducible, and destroyable in one
  command. This is the "more effective alternative."

Both produce the identical VM + static IP + firewall + backup bucket. After
either, you still run Parts 5–14 on the VM (or automate them — see A.3).

## A.1 — gcloud CLI

Install the Google Cloud CLI on your own computer
(<https://cloud.google.com/sdk/docs/install>), or use **Cloud Shell** (the `>_`
icon in the console — it has `gcloud` preinstalled, nothing to install).

```bash
# --- variables: edit these three ---
PROJECT="retailops-$RANDOM"          # globally-unique project id
REGION="us-central1"                 # free-tier region
ZONE="us-central1-a"

# --- project + billing + API ---
gcloud projects create "$PROJECT"
gcloud config set project "$PROJECT"
# link billing (list accounts, then link the first one)
ACCT=$(gcloud billing accounts list --format="value(name)" | head -n1)
gcloud billing projects link "$PROJECT" --billing-account="$ACCT"
gcloud services enable compute.googleapis.com storage.googleapis.com

# --- static IP ---
gcloud compute addresses create retailops-ip --region="$REGION"
IP=$(gcloud compute addresses describe retailops-ip --region="$REGION" --format="value(address)")
echo "Static IP: $IP"
echo "Your nip.io host: ${IP//./-}.nip.io"

# --- VM (Debian 12, e2-small, http/https tags) ---
gcloud compute instances create retailops-vm \
  --zone="$ZONE" \
  --machine-type=e2-small \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=30GB \
  --address=retailops-ip \
  --tags=http-server,https-server

# --- firewall: GCP auto-creates default-allow-http/https for those tags.
#     If missing, create them explicitly: ---
gcloud compute firewall-rules create allow-http  --allow=tcp:80  --target-tags=http-server  || true
gcloud compute firewall-rules create allow-https --allow=tcp:443 --target-tags=https-server || true

# --- backup bucket ---
gcloud storage buckets create "gs://retailops-backups-$PROJECT" --location=US

# --- connect ---
gcloud compute ssh retailops-vm --zone="$ZONE"
```

`gcloud compute ssh` opens the same terminal as the console **SSH** button.
Continue with Part 5.

**Tear everything down (stop all charges):**
```bash
gcloud compute instances delete retailops-vm --zone="$ZONE" --quiet
gcloud compute addresses delete retailops-ip --region="$REGION" --quiet
gcloud storage rm -r "gs://retailops-backups-$PROJECT"   # deletes backups too
```

## A.2 — Terraform (recommended for repeatable setups)

Terraform describes the whole infrastructure in a file and creates/destroys it
with one command. Install Terraform, authenticate once with
`gcloud auth application-default login`, then save this as `main.tf`:

```hcl
terraform {
  required_providers { google = { source = "hashicorp/google" } }
}

variable "project" {}
variable "region" { default = "us-central1" }
variable "zone"   { default = "us-central1-a" }

provider "google" {
  project = var.project
  region  = var.region
  zone    = var.zone
}

resource "google_compute_address" "ip" {
  name   = "retailops-ip"
  region = var.region
}

resource "google_compute_firewall" "web" {
  name          = "allow-web"
  network       = "default"
  allow { protocol = "tcp" ports = ["80", "443"] }
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["http-server", "https-server"]
}

resource "google_compute_instance" "vm" {
  name         = "retailops-vm"
  machine_type = "e2-small"
  tags         = ["http-server", "https-server"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 30
    }
  }

  network_interface {
    network = "default"
    access_config { nat_ip = google_compute_address.ip.address }
  }
}

resource "google_storage_bucket" "backups" {
  name     = "retailops-backups-${var.project}"
  location = "US"
}

output "nip_io_host" {
  value = "${replace(google_compute_address.ip.address, ".", "-")}.nip.io"
}
```

Run:
```bash
terraform init
terraform apply -var="project=YOUR_PROJECT_ID"     # type 'yes' to confirm
# Terraform prints nip_io_host = 34-12-56-78.nip.io
terraform destroy -var="project=YOUR_PROJECT_ID"   # later: removes everything
```

Why better: one source of truth, `apply` is idempotent (safe to re-run),
`destroy` guarantees nothing is left billing.

## A.3 — Fully automated (one command, zero SSH)

Both gcloud and Terraform can run the **entire server setup** (Parts 5–14)
automatically at first boot via a **startup script**. Put Parts 5–14 commands
into a file `setup.sh`, then:

- **gcloud:** add `--metadata-from-file=startup-script=setup.sh` to the
  `instances create` command.
- **Terraform:** add `metadata_startup_script = file("setup.sh")` inside the
  `google_compute_instance` block.

The VM then provisions itself; you only collect the kiosk key from the logs:
`gcloud compute ssh retailops-vm -- 'sudo journalctl -u google-startup-scripts'`.
Keep secrets (DB password, secret key) out of the script — pass them as separate
metadata or a Secret Manager reference.

---

# Appendix B — Self-hosted object storage with RustFS (S3-compatible)

The main guide stores uploaded images on the VM's **local disk** (simplest, good
for a pilot). **RustFS** is an alternative: a single-binary, S3-compatible object
store (a lightweight MinIO-style server, written in Rust) that runs on the same
VM. The backend already speaks S3 (`MEDIA_STORAGE_BACKEND=s3`), so it can use
RustFS with no code change.

**Use RustFS instead of local disk when you want:** the S3 API (signed URLs,
per-object ACLs, separate public/private buckets for product images vs
receipts), or a path to move storage to a bigger/standalone box later without
changing the app.

> For a single-store pilot, local disk (main guide) is less moving parts. Adopt
> RustFS only if you need the above. Exact RustFS flag/env names evolve — confirm
> against the current RustFS docs (<https://rustfs.com>) if a command differs.

## B.1 — Install RustFS as a service

```bash
# data directory + dedicated user
sudo useradd --system --create-home --home-dir /var/lib/rustfs --shell /usr/sbin/nologin rustfs
sudo mkdir -p /var/lib/rustfs/data
sudo chown -R rustfs:rustfs /var/lib/rustfs

# download the RustFS server binary (check rustfs.com for the current URL)
curl -fsSL https://dl.rustfs.com/rustfs-linux-amd64 -o /tmp/rustfs
sudo install -m 0755 /tmp/rustfs /usr/local/bin/rustfs
```

Credentials file `/etc/rustfs.env` — **choose a strong access key + secret**:

```bash
sudo tee /etc/rustfs.env >/dev/null <<'EOF'
RUSTFS_ACCESS_KEY=RUSTFS_ACCESS
RUSTFS_SECRET_KEY=RUSTFS_SECRET
RUSTFS_ADDRESS=127.0.0.1:9000
RUSTFS_VOLUMES=/var/lib/rustfs/data
EOF
sudo chmod 640 /etc/rustfs.env
sudo chown root:rustfs /etc/rustfs.env
```

systemd service so it stays running:

```bash
sudo tee /etc/systemd/system/rustfs.service >/dev/null <<'EOF'
[Unit]
Description=RustFS object storage
After=network.target

[Service]
User=rustfs
Group=rustfs
EnvironmentFile=/etc/rustfs.env
ExecStart=/usr/local/bin/rustfs
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now rustfs
sudo systemctl status rustfs --no-pager
```

RustFS now serves the S3 API on `127.0.0.1:9000` (internal only).

## B.2 — Create the two buckets

Use the AWS CLI as an S3 client (works with any S3-compatible server):

```bash
sudo apt-get -y install awscli
export AWS_ACCESS_KEY_ID=RUSTFS_ACCESS
export AWS_SECRET_ACCESS_KEY=RUSTFS_SECRET
export AWS_DEFAULT_REGION=us-east-1
EP="http://127.0.0.1:9000"

aws --endpoint-url "$EP" s3 mb s3://retailops-products
aws --endpoint-url "$EP" s3 mb s3://retailops-receipts
aws --endpoint-url "$EP" s3 ls
```

## B.3 — Point the backend at RustFS

Public image URLs must be reachable by phones, so the S3 endpoint the app
**generates URLs from** must be your public HTTPS host, not `127.0.0.1`. We use
**path-style** addressing (`https://HOST/bucket/key`) and let Caddy route the two
bucket paths to RustFS.

Edit `/etc/retailops.env` — replace the local-media lines from Part 8 with:

```bash
# remove: MEDIA_STORAGE_BACKEND=local  and  MEDIA_ROOT=...
MEDIA_STORAGE_BACKEND=s3
MEDIA_S3_ENDPOINT_URL=https://HOST_NIPIO
MEDIA_S3_ACCESS_KEY_ID=RUSTFS_ACCESS
MEDIA_S3_SECRET_ACCESS_KEY=RUSTFS_SECRET
MEDIA_S3_REGION_NAME=us-east-1
MEDIA_S3_ADDRESSING_STYLE=path
MEDIA_S3_PRODUCT_BUCKET_NAME=retailops-products
MEDIA_S3_RECEIPT_BUCKET_NAME=retailops-receipts
MEDIA_S3_PRODUCT_PUBLIC=True
MEDIA_S3_RECEIPT_SIGNED_URLS=True
```

Then `sudo systemctl restart retailops`.

## B.4 — Route the bucket paths through Caddy

Edit `/etc/caddy/Caddyfile`. **Remove** the `handle_path /media/*` block (media
no longer on local disk) and **add** a rule that sends the two bucket paths to
RustFS. Keep `/static/*` and the final `reverse_proxy`:

```caddy
HOST_NIPIO {
    encode gzip

    handle_path /static/* {
        root * /opt/retailops/app/staticfiles
        file_server
    }

    @s3 path /retailops-products/* /retailops-receipts/*
    handle @s3 {
        reverse_proxy 127.0.0.1:9000
    }

    reverse_proxy 127.0.0.1:8000
}
```

`sudo systemctl restart caddy`. Now `https://HOST_NIPIO/retailops-products/...`
serves public product images, and receipt access uses time-limited **signed
URLs** validated by RustFS. Product images are public; receipts stay private.

## B.5 — Back up RustFS instead of the local media folder

In `/opt/retailops/backup.sh`, replace the `tar ... media` line with a copy of
the RustFS data directory to the backup bucket:

```bash
# replace the media tar line with:
gcloud storage rsync -r /var/lib/rustfs/data "$BUCKET/rustfs-data"
```

(Backing up the raw RustFS data directory is simplest; alternatively use
`aws s3 sync` per bucket through the endpoint.)

## B.6 — Security notes (RustFS)

- RustFS listens on `127.0.0.1` only; the internet reaches it **only** through
  Caddy's two bucket-path rules over HTTPS. Never expose port 9000 publicly.
- Treat `RUSTFS_SECRET_KEY` like a password — it is full admin to all objects.
- Receipts contain customer payment proof — keep `MEDIA_S3_RECEIPT_SIGNED_URLS=True`
  (private, signed) so they are never publicly listable.
