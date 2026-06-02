# OCR Receipt Flow Manual Test

Use this checklist after applying migrations and installing test/runtime dependencies.

## Setup

1. Set `ocr_enabled=true` in Admin settings.
2. Set `ocr_base_url=https://vepay-api-5bja335wiq-pv.a.run.app`.
3. Retrieve the API key from Secret Manager:

   ```powershell
   gcloud secrets versions access latest --secret vepay-api-key --project vepay-api-20260503
   ```

4. Paste the key into the VEPay API key field.
5. Enable OCR for `mobile_payment`; optionally enable `bank_transfer`.
6. Confirm the server-side connection test succeeds. The VEPay client tries `/health` first and falls back to `/healthz`.

## Happy Path

1. Boot Django and the kiosk static server.
2. Start a kiosk purchase and place a Bs. 963,89 order.
3. Select `Pago movil`.
4. Upload a real BDV receipt screenshot for the exact amount.
5. Confirm the receipt form pre-fills, locks verified fields, and allows checkout.
6. Confirm the resulting payment stores `transaction_key`, receipt metadata, and an `OcrCallLog(status='success')`.

## Negative Cases

1. Upload a valid receipt with a different amount. Expect `422 amount_mismatch` and an `OcrCallLog(status='amount_mismatch')`.
2. Re-upload a receipt whose `transaction_key` already exists. Expect `409 duplicate_transaction`.
3. Toggle `ocr_enabled=false`. The kiosk should still show the manual receipt form without the upload card.
4. Complete manual entry. The checkout should create a `pending_review` payment.
5. Upload an unsupported HEIC/HEIF image on a server without HEIF support. Expect `415 unsupported_heif`.

## Retention

Run a dry-run purge before wiring the command to cron:

```powershell
python manage.py purge_receipts --dry-run
```

Then run the actual purge when the count looks correct:

```powershell
python manage.py purge_receipts
```
