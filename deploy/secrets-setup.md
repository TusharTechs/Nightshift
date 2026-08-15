# Secrets setup: Google Secret Manager → VM `.env`

NightShift AI reads all configuration from environment variables via
`services/core_api/app/config.py` (`Settings`), which is loaded from a `.env`
file (see `.env.example` for the local-dev template). In production, that
`.env` file lives only on the VM's disk — it is never committed — and its
contents are sourced from Google Secret Manager at deploy time.

## 1. Enable the API and create a service account for the VM

```bash
gcloud services enable secretmanager.googleapis.com --project="${PROJECT_ID}"

gcloud iam service-accounts create nightshift-vm \
  --project="${PROJECT_ID}" \
  --display-name="NightShift AI Compute Engine VM"

SA_EMAIL="nightshift-vm@${PROJECT_ID}.iam.gserviceaccount.com"
```

A plain Compute Engine VM has no access to Secret Manager by default — the
attached service account needs the `secretmanager.secretAccessor` role
explicitly granted, project-wide (or per-secret, if you want tighter
scoping):

```bash
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/secretmanager.secretAccessor"
```

This `SA_EMAIL` is what `deploy/gcp-vm-setup.sh` attaches to the VM via
`--service-account` and `--scopes=cloud-platform` — do this step **before**
running that script.

## 2. Create one secret per sensitive value

Every env var below is read somewhere in `services/core_api/app/config.py`.
Non-sensitive ones (feature flags, model names, numeric tuning knobs) don't
need to go through Secret Manager — hardcode those directly in the VM's
`.env` or leave them at their code defaults. The genuinely sensitive ones:

| Secret Manager name | Env var it becomes | Notes |
|---|---|---|
| `nightshift-postgres-password` | `POSTGRES_PASSWORD` | New for prod — see `deploy/docker-compose.prod.yml`; local dev hardcodes `nightshift_dev_password`, prod must not |
| `nightshift-shopify-client-id` | `SHOPIFY_APP_CLIENT_ID` | From the Shopify Partner Dashboard |
| `nightshift-shopify-client-secret` | `SHOPIFY_APP_SECRET` | From the Shopify Partner Dashboard |
| `nightshift-kms-key-id` | `KMS_KEY_ID` | GCP KMS key resource name, if you wire up real KMS envelope encryption |
| `nightshift-local-data-key` | `NIGHTSHIFT_LOCAL_DATA_KEY` | Base64 32-byte key — only used as a KMS fallback; generate a real one for prod, don't reuse the dev key |
| `nightshift-google-ai-api-key` | `GOOGLE_AI_API_KEY` | Gemini (embeddings + Chief Ops AI's hard-wired Gemini call, and the default LLM provider for per-specialist detection) |
| `nightshift-caddy-tls-email` | `CADDY_TLS_EMAIL` | Let's Encrypt account email (not secret, but keep it consistent with the rest) |

Example for one secret (repeat per row above):

```bash
printf '%s' 'the-actual-secret-value' | \
  gcloud secrets create nightshift-postgres-password \
    --project="${PROJECT_ID}" \
    --replication-policy="automatic" \
    --data-file=-
```

To rotate a value later (new version, old versions stay retrievable):

```bash
printf '%s' 'the-new-value' | \
  gcloud secrets versions add nightshift-postgres-password \
    --project="${PROJECT_ID}" \
    --data-file=-
```

## 3. Materialize secrets into the VM's `.env` at deploy time

Run this **on the VM** (it has the `nightshift-vm` service account attached,
so `gcloud secrets versions access` works without a separate key file). This
is the snippet to call from your deploy script or a systemd unit's
`ExecStartPre=` before `docker compose up -d`:

```bash
#!/usr/bin/env bash
# deploy/fetch-secrets.sh — run on the VM, writes /opt/nightshift-ai/.env
set -euo pipefail

PROJECT_ID="your-project-id"
ENV_FILE="/opt/nightshift-ai/.env"

fetch() { gcloud secrets versions access latest --project="${PROJECT_ID}" --secret="$1"; }

{
  echo "# Generated $(date -u +%FT%TZ) from Secret Manager — do not edit by hand, do not commit."
  echo "POSTGRES_PASSWORD=$(fetch nightshift-postgres-password)"
  echo "SHOPIFY_APP_CLIENT_ID=$(fetch nightshift-shopify-client-id)"
  echo "SHOPIFY_APP_SECRET=$(fetch nightshift-shopify-client-secret)"
  echo "KMS_KEY_ID=$(fetch nightshift-kms-key-id)"
  echo "NIGHTSHIFT_LOCAL_DATA_KEY=$(fetch nightshift-local-data-key)"
  echo "GOOGLE_AI_API_KEY=$(fetch nightshift-google-ai-api-key)"
  echo "CADDY_TLS_EMAIL=$(fetch nightshift-caddy-tls-email)"
  # Non-secret production settings — plain values, no Secret Manager needed.
  echo "ENVIRONMENT=production"
  echo "SHOPIFY_APP_URL=https://\${CADDY_DOMAIN}"
  echo "CADDY_DOMAIN=your-domain.example.com"
  echo "SHOPIFY_API_VERSION=2024-07"
  echo "LLM_PROVIDER=GEMINI"
  echo "CHIEF_OPS_LLM_PROVIDER=GEMINI"
  echo "DEMO_MODE_ENABLED=false"
  echo "SHIFT_SCHEDULE_ENABLED=true"
  echo "SHIFT_SCHEDULE_INTERVAL_MINUTES=1440"
  echo "LLM_MAX_CALLS_PER_DAY=200"
  echo "LLM_MAX_OUTPUT_TOKENS=4096"
  echo "LLM_MAX_FINDINGS_PER_CALL=50"
  echo "LOG_LEVEL=INFO"
} > "${ENV_FILE}"

chmod 600 "${ENV_FILE}"
echo "Wrote ${ENV_FILE}"
```

Fill in the real `CADDY_DOMAIN` and double-check `SHOPIFY_APP_URL` before
running `docker compose up`. `chmod 600` matters — this file holds live API
keys and a DB password on a multi-user-capable VM.

If you'd rather wire this as a systemd unit instead of running it by hand
each deploy:

```ini
# /etc/systemd/system/nightshift-ai.service (illustrative — adjust paths)
[Unit]
Description=NightShift AI (docker compose)
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/nightshift-ai
ExecStartPre=/opt/nightshift-ai/deploy/fetch-secrets.sh
ExecStart=/usr/bin/docker compose -f deploy/docker-compose.prod.yml --env-file .env up -d
ExecStop=/usr/bin/docker compose -f deploy/docker-compose.prod.yml down

[Install]
WantedBy=multi-user.target
```

## 4. Never commit `.env`

`.gitignore` should already exclude `.env` (matching local dev's convention
of committing only `.env.example`). Double-check before your first prod
commit from this VM's checkout — a committed `.env` here would leak live API
keys and a DB password, not just dev placeholders.
