# RetailOps Media Storage Configuration

RetailOps stores file bytes through Django's storage API. The database only
stores object names such as `products/2026/05/SKU-uuid.jpg` or
`receipts/2026/05/SO-uuid.jpg`.

By default, files stay local under `media/`. For a shared media platform, set
environment variables before Django starts and RetailOps will write media to
Google Cloud Storage or S3-compatible storage without changing the product,
payment, kiosk, or API code.

## Recommended Architecture

Use an infrastructure layer that is independent from RetailOps:

- Public product assets bucket, for example `<product-assets-bucket>`
- Private document bucket, for example `<receipt-documents-bucket>`
- Dedicated service account for application writes
- Optional public CDN/custom domain for product images
- Signed URLs for private receipts and documents

RetailOps is only a client of this layer. Other systems can use the same buckets
or their own prefixes with separate service accounts.

## Local Filesystem Profile

No media variables are required.

```bash
python manage.py runserver
```

Defaults:

- `MEDIA_STORAGE_BACKEND=local`
- `MEDIA_ROOT=<repo>/media`
- `MEDIA_URL=media/`

When local storage is active and `DEBUG=True`, Django serves uploaded media from
`MEDIA_URL`.

## Google Cloud Storage Profile

Install dependencies:

```bash
pip install -r requirements.txt
```

Set the storage profile:

```bash
export MEDIA_STORAGE_BACKEND=gcs
export MEDIA_GCS_PROJECT_ID="<media-project-id>"
export MEDIA_GCS_PUBLIC_BUCKET_NAME="<product-assets-bucket>"
export MEDIA_GCS_PRIVATE_BUCKET_NAME="<receipt-documents-bucket>"
python manage.py check
```

Routing is automatic:

| Object prefix | Bucket | URL behavior |
| --- | --- | --- |
| `products/` | `MEDIA_GCS_PRODUCT_BUCKET_NAME` or `MEDIA_GCS_PUBLIC_BUCKET_NAME` | public URL by default |
| `receipts/` | `MEDIA_GCS_RECEIPT_BUCKET_NAME` or `MEDIA_GCS_PRIVATE_BUCKET_NAME` | signed URL by default |
| any other prefix | `MEDIA_GCS_DEFAULT_BUCKET_NAME` or product bucket | signed URL by default |

If you prefer one bucket with prefixes:

```bash
export MEDIA_STORAGE_BACKEND=gcs
export MEDIA_GCS_BUCKET_NAME="<media-bucket>"
python manage.py check
```

Two buckets remain the recommended default because product images are safe to
serve publicly while receipts are payment evidence and should stay private.

## S3-Compatible Local Profile

Use this profile for local/bare-metal object storage such as RustFS or Garage.
RustFS is the recommended default for a simple single-node local service.
Garage uses the same RetailOps backend with a different endpoint.

Install dependencies:

```bash
pip install -r requirements.txt
```

Set the storage profile directly:

```bash
export MEDIA_STORAGE_BACKEND=s3
export MEDIA_S3_ENDPOINT_URL=http://127.0.0.1:9000
export MEDIA_S3_ACCESS_KEY_ID="<s3-access-key>"
export MEDIA_S3_SECRET_ACCESS_KEY="<s3-secret-key>"
export MEDIA_S3_PUBLIC_BUCKET_NAME="<product-assets-bucket>"
export MEDIA_S3_PRIVATE_BUCKET_NAME="<receipt-documents-bucket>"
python manage.py check
```

Routing is automatic:

| Object prefix | Bucket | URL behavior |
| --- | --- | --- |
| `products/` | `MEDIA_S3_PRODUCT_BUCKET_NAME` or `MEDIA_S3_PUBLIC_BUCKET_NAME` | public URL by default |
| `receipts/` | `MEDIA_S3_RECEIPT_BUCKET_NAME` or `MEDIA_S3_PRIVATE_BUCKET_NAME` | signed URL by default |
| any other prefix | `MEDIA_S3_DEFAULT_BUCKET_NAME` or product bucket | signed URL by default |

If you prefer one bucket with prefixes:

```bash
export MEDIA_STORAGE_BACKEND=s3
export MEDIA_S3_ENDPOINT_URL=http://127.0.0.1:9000
export MEDIA_S3_ACCESS_KEY_ID="<s3-access-key>"
export MEDIA_S3_SECRET_ACCESS_KEY="<s3-secret-key>"
export MEDIA_S3_BUCKET_NAME="<media-bucket>"
python manage.py check
```

Provision the recommended two-bucket layout against an existing RustFS or Garage
service with AWS CLI:

```bash
export RETAILOPS_S3_ACCESS_KEY_ID="<s3-access-key>"
export RETAILOPS_S3_SECRET_ACCESS_KEY="<s3-secret-key>"
./scripts/provision-retailops-local-s3.sh --s3-provider rustfs
```

Garage uses the same command with `--s3-provider garage`. For any other
endpoint, use `--s3-provider custom --s3-endpoint-url "http://host:port"`.

Windows / PowerShell equivalent:

```powershell
$env:RETAILOPS_S3_ACCESS_KEY_ID = "<s3-access-key>"
$env:RETAILOPS_S3_SECRET_ACCESS_KEY = "<s3-secret-key>"
powershell -ExecutionPolicy Bypass -File .\scripts\provision-retailops-local-s3.ps1 -S3Provider rustfs
```

PowerShell uses `-S3Provider garage` for Garage and
`-S3Provider custom -S3EndpointUrl "http://host:port"` for any other endpoint.

## Local Startup Combinations

Use `scripts/start-retailops.sh` to choose database and storage layers
independently.

```bash
# Local backend + local SQLite + local media.
./scripts/start-retailops.sh

# Local backend + Cloud SQL + local media.
./scripts/start-retailops.sh --db-mode cloud

# Local backend + local SQLite + Google Cloud Storage.
./scripts/start-retailops.sh --storage-mode cloud

# Local backend + local SQLite + S3-compatible media.
RETAILOPS_S3_ACCESS_KEY_ID="<s3-access-key>" \
RETAILOPS_S3_SECRET_ACCESS_KEY="<s3-secret-key>" \
  ./scripts/start-retailops.sh --storage-mode s3 --s3-provider rustfs

# Local backend + local PostgreSQL + S3-compatible media.
RETAILOPS_POSTGRES_PASSWORD="<local-postgres-password>" \
RETAILOPS_S3_ACCESS_KEY_ID="<s3-access-key>" \
RETAILOPS_S3_SECRET_ACCESS_KEY="<s3-secret-key>" \
  ./scripts/start-retailops.sh --db-mode postgres --storage-mode s3 --s3-provider rustfs

# Local backend + local PostgreSQL + Garage media.
RETAILOPS_POSTGRES_PASSWORD="<local-postgres-password>" \
RETAILOPS_S3_ACCESS_KEY_ID="<s3-access-key>" \
RETAILOPS_S3_SECRET_ACCESS_KEY="<s3-secret-key>" \
  ./scripts/start-retailops.sh --db-mode postgres --storage-mode s3 --s3-provider garage

# Local backend + Cloud SQL + Google Cloud Storage.
./scripts/start-retailops.sh --db-mode cloud --storage-mode cloud
```

Windows / PowerShell equivalents:

```powershell
# Local backend + local SQLite + local media.
powershell -ExecutionPolicy Bypass -File .\scripts\start-retailops.ps1

# Local backend + Cloud SQL + local media.
powershell -ExecutionPolicy Bypass -File .\scripts\start-retailops.ps1 -DbMode cloud

# Local backend + local SQLite + Google Cloud Storage.
powershell -ExecutionPolicy Bypass -File .\scripts\start-retailops.ps1 -StorageMode cloud

# Local backend + local SQLite + S3-compatible media.
$env:RETAILOPS_S3_ACCESS_KEY_ID = "<s3-access-key>"
$env:RETAILOPS_S3_SECRET_ACCESS_KEY = "<s3-secret-key>"
powershell -ExecutionPolicy Bypass -File .\scripts\start-retailops.ps1 -StorageMode s3 -S3Provider rustfs

# Local backend + local PostgreSQL + S3-compatible media.
$env:RETAILOPS_POSTGRES_PASSWORD = "<local-postgres-password>"
$env:RETAILOPS_S3_ACCESS_KEY_ID = "<s3-access-key>"
$env:RETAILOPS_S3_SECRET_ACCESS_KEY = "<s3-secret-key>"
powershell -ExecutionPolicy Bypass -File .\scripts\start-retailops.ps1 -DbMode postgres -StorageMode s3 -S3Provider rustfs

# Local backend + Cloud SQL + Google Cloud Storage.
powershell -ExecutionPolicy Bypass -File .\scripts\start-retailops.ps1 -DbMode cloud -StorageMode cloud
```

Cloud storage values used by the script:

- media project: `<media-project-id>`
- public product bucket: `<product-assets-bucket>`
- private receipt bucket: `<receipt-documents-bucket>`
- media credentials: `~/.config/retailops/media/<service-account-key>.json`

Pass these values explicitly for reusable deployments instead of relying on any
workstation-specific defaults in the helper script.

Override them when needed:

```bash
./scripts/start-retailops.sh \
  --storage-mode cloud \
  --media-project-id my-media-project \
  --media-public-bucket-name my-public-assets \
  --media-private-bucket-name my-private-documents \
  --media-credentials-file "$HOME/.config/retailops/media/my-media-client.json"
```

S3-compatible defaults used by the script:

- RustFS endpoint: `http://127.0.0.1:9000`
- Garage endpoint: `http://127.0.0.1:3900`
- product bucket: `<product-assets-bucket>`
- receipt bucket: `<receipt-documents-bucket>`
- region: `us-east-1`
- address style: `path`
- signature version: `s3v4`

Set credentials outside the repo before using `--storage-mode s3`:

```bash
export RETAILOPS_S3_ACCESS_KEY_ID="<s3-access-key>"
export RETAILOPS_S3_SECRET_ACCESS_KEY="<s3-secret-key>"
```

## Public Product Bucket

The default assumes the product bucket is public at the bucket/IAM or bucket
policy layer:

```bash
export MEDIA_GCS_PRODUCT_PUBLIC=true
export MEDIA_S3_PRODUCT_PUBLIC=true
```

For a bucket using uniform public access, keep `MEDIA_GCS_PRODUCT_DEFAULT_ACL`
unset. If you intentionally use fine-grained object ACLs, set:

```bash
export MEDIA_GCS_PRODUCT_DEFAULT_ACL=publicRead
```

Product uploads use long-lived cache headers by default because uploaded object
names include UUIDs:

```bash
export MEDIA_GCS_PRODUCT_CACHE_CONTROL="public, max-age=31536000, immutable"
export MEDIA_S3_PRODUCT_CACHE_CONTROL="public, max-age=31536000, immutable"
```

Optional custom endpoint or CDN domain:

```bash
export MEDIA_GCS_PUBLIC_CUSTOM_ENDPOINT=https://assets.example.com
```

## Private Receipt Bucket

Receipts use signed URLs by default. A signed URL is a bearer URL: anyone who
has the generated link can read that object until the URL expires. Keep
expiration short, do not log or share receipt URLs unnecessarily, and continue
to enforce user permissions before exposing the link from RetailOps.

```bash
export MEDIA_GCS_RECEIPT_SIGNED_URLS=true
export MEDIA_GCS_SIGNED_URL_EXPIRATION=900
export MEDIA_S3_RECEIPT_SIGNED_URLS=true
export MEDIA_S3_SIGNED_URL_EXPIRATION=900
```

Receipt uploads use private no-store cache headers by default:

```bash
export MEDIA_GCS_RECEIPT_CACHE_CONTROL="private, max-age=0, no-store"
export MEDIA_S3_RECEIPT_CACHE_CONTROL="private, max-age=0, no-store"
```

For Cloud Run or other Google compute without a mounted private key, signed URLs
should use IAM Sign Blob:

```bash
export MEDIA_GCS_IAM_SIGN_BLOB=true
export MEDIA_GCS_SERVICE_ACCOUNT_EMAIL="<signer-service-account>@<media-project-id>.iam.gserviceaccount.com"
```

The runtime service account needs storage object permissions for the configured
buckets and permission to call IAM Sign Blob for the configured signing service
account. In Google Cloud IAM terms, grant only the minimum roles needed to write
objects and sign blobs; do not give broad project-owner permissions just to make
signed URLs work.

For local development with a service account key, set the standard Google
credential variable outside the repo:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/.config/retailops/media/<service-account-key>.json"
```

Do not commit credential files.

## Useful Environment Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `MEDIA_STORAGE_BACKEND` | `local` | `local`, `gcs`, or `s3` |
| `MEDIA_ROOT` | `<repo>/media` | local filesystem root |
| `MEDIA_URL` | `media/` | local media URL prefix |
| `MEDIA_GCS_PROJECT_ID` | inferred by Google SDK | GCP project |
| `MEDIA_GCS_BUCKET_NAME` | none | one-bucket fallback |
| `MEDIA_GCS_PUBLIC_BUCKET_NAME` | `MEDIA_GCS_BUCKET_NAME` | product image bucket |
| `MEDIA_GCS_PRIVATE_BUCKET_NAME` | `MEDIA_GCS_BUCKET_NAME` | receipt/document bucket |
| `MEDIA_GCS_DEFAULT_BUCKET_NAME` | generic/product bucket | fallback bucket |
| `MEDIA_GCS_PRODUCT_PUBLIC` | `true` | product URLs are unsigned |
| `MEDIA_GCS_RECEIPT_SIGNED_URLS` | `true` | receipt URLs are signed |
| `MEDIA_GCS_SIGNED_URL_EXPIRATION` | `900` | signed URL lifetime in seconds |
| `MEDIA_GCS_FILE_OVERWRITE` | `false` | avoid accidental overwrite |
| `MEDIA_GCS_IAM_SIGN_BLOB` | `false` | sign URLs through IAM |
| `MEDIA_GCS_SERVICE_ACCOUNT_EMAIL` | none | service account used for signing |
| `MEDIA_S3_ENDPOINT_URL` | none | RustFS/Garage/custom S3 endpoint |
| `MEDIA_S3_ACCESS_KEY_ID` | none | S3 access key |
| `MEDIA_S3_SECRET_ACCESS_KEY` | none | S3 secret key |
| `MEDIA_S3_REGION_NAME` | `us-east-1` | S3 signing region |
| `MEDIA_S3_BUCKET_NAME` | none | one-bucket fallback |
| `MEDIA_S3_PUBLIC_BUCKET_NAME` | `MEDIA_S3_BUCKET_NAME` | product image bucket |
| `MEDIA_S3_PRIVATE_BUCKET_NAME` | `MEDIA_S3_BUCKET_NAME` | receipt/document bucket |
| `MEDIA_S3_DEFAULT_BUCKET_NAME` | generic/product bucket | fallback bucket |
| `MEDIA_S3_PRODUCT_PUBLIC` | `true` | product URLs are unsigned |
| `MEDIA_S3_RECEIPT_SIGNED_URLS` | `true` | receipt URLs are signed |
| `MEDIA_S3_SIGNED_URL_EXPIRATION` | `900` | signed URL lifetime in seconds |
| `MEDIA_S3_ADDRESSING_STYLE` | `path` | local-provider-friendly URL style |
| `MEDIA_S3_SIGNATURE_VERSION` | `s3v4` | request signing version |
| `MEDIA_S3_FILE_OVERWRITE` | `false` | avoid accidental overwrite |

## Migrating Existing Local Media

1. Stop Django processes that can upload files.
2. Back up the local `media/` directory.
3. Upload product files to the public bucket and receipt files to the private
   bucket while preserving their relative paths.

```bash
export PRODUCT_ASSETS_BUCKET="<product-assets-bucket>"
export RECEIPT_DOCUMENTS_BUCKET="<receipt-documents-bucket>"
gcloud storage cp ./media/products "gs://${PRODUCT_ASSETS_BUCKET}/products/" --recursive
gcloud storage cp ./media/receipts "gs://${RECEIPT_DOCUMENTS_BUCKET}/receipts/" --recursive
```

For S3-compatible storage, use AWS CLI with the local endpoint:

```bash
export PRODUCT_ASSETS_BUCKET="<product-assets-bucket>"
export RECEIPT_DOCUMENTS_BUCKET="<receipt-documents-bucket>"
aws --endpoint-url http://127.0.0.1:9000 s3 sync ./media/products/ "s3://${PRODUCT_ASSETS_BUCKET}/products/"
aws --endpoint-url http://127.0.0.1:9000 s3 sync ./media/receipts/ "s3://${RECEIPT_DOCUMENTS_BUCKET}/receipts/"
```

4. Start Django with `MEDIA_STORAGE_BACKEND=gcs` or `MEDIA_STORAGE_BACKEND=s3`.
5. Open product thumbnails and receipt links from the back office.

No database update is needed when object names are preserved.

## Verification

```bash
python manage.py check
python manage.py test retailops.tests.test_media_storage_config
```

Manual checks:

- Create or edit a product with an uploaded image.
- Confirm the product object is written under `products/`.
- Confirm inventory, orders, and kiosk show the product image URL.
- Submit a receipt-based kiosk payment.
- Confirm the receipt object is written under `receipts/`.
- Confirm receipt URLs are signed and expire.

## Operational Notes

- Keep product and receipt buckets separate unless cost or account limits force a
  one-bucket layout.
- Do not let kiosks or other clients write directly to the bucket unless you add
  a separate signed-upload service.
- Use lifecycle rules on private receipts if legal retention permits automated
  deletion or archival.
- A centralized database does not centralize file bytes; this storage layer is
  what makes uploaded images available across devices and servers.
