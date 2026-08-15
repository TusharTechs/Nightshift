#!/usr/bin/env bash
#
# deploy/gcp-vm-setup.sh — NightShift AI: provision the single Compute
# Engine VM this app runs on, plus firewall rules and a budget alert.
#
# THIS IS A REFERENCE SCRIPT, NOT A curl|bash INSTALLER.
# Read every section before running. It is written to be idempotent (each
# `gcloud ... create` is preceded by a `describe`/existence check so
# re-running this script after a partial failure, or to change one value,
# doesn't blow up on "already exists") but it still makes real, billable
# changes to your GCP project — run it a section at a time and read the
# output.
#
# Prerequisites:
#   - `gcloud` CLI installed and authenticated: `gcloud auth login`
#   - A GCP project selected: `gcloud config set project YOUR_PROJECT_ID`
#   - Billing enabled on the project
#   - Compute Engine API enabled: `gcloud services enable compute.googleapis.com`
#
# Fill these in before running:

set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID, e.g. export PROJECT_ID=my-gcp-project}"
REGION="${REGION:-us-central1}"
ZONE="${ZONE:-us-central1-a}"
VM_NAME="${VM_NAME:-nightshift-ai-prod}"

# --- VM sizing --------------------------------------------------------------
# e2-medium: 2 vCPU (shared core), 4 GB RAM. Chosen over e2-small (2 vCPU
# shared, 2 GB RAM) because this VM runs Postgres + Redis + the FastAPI
# process (core_api) + two Celery processes (worker, beat) + Caddy
# concurrently — five-plus long-lived processes plus the OS and Docker
# daemon itself comfortably exceed 2 GB once Postgres's shared_buffers and
# the two Python (Celery/FastAPI) processes' RSS are accounted for. 2 GB
# would work only under very light load and risks the OOM killer under any
# real traffic or a catalog-scan burst. 4 GB gives real headroom.
# Tradeoff: e2-medium costs roughly 2x e2-small (~$24-30/mo vs ~$12-14/mo
# on-demand in most US regions as of 2026 — check
# https://cloud.google.com/compute/all-pricing for current numbers; both
# drop further with a 1-year or 3-year committed-use discount). Given
# Postgres + Redis + 2 Python processes running together, e2-medium is the
# safer choice for this workload; e2-small is not recommended for this
# service mix.
MACHINE_TYPE="${MACHINE_TYPE:-e2-medium}"

# Standard Ubuntu (not Container-Optimized OS) + a startup-script that
# installs Docker Engine and the Docker Compose plugin. This is chosen over
# `gcloud compute instances create-with-container` (which runs exactly one
# container per VM via the Konlet agent, and would need painful workarounds
# to run 6 coordinated docker-compose services with a private network and
# shared volumes) and over Container-Optimized OS (COS ships Docker but not
# a persistent, easily-editable startup toolchain for docker-compose-style
# multi-container orchestration, and its read-only root filesystem
# complicates ad-hoc debugging). A standard Ubuntu 22.04 LTS image with
# Docker + Compose installed via startup-script is the most reliable, most
# ops-familiar option for a docker-compose multi-container deployment.
IMAGE_FAMILY="${IMAGE_FAMILY:-ubuntu-2204-lts}"
IMAGE_PROJECT="${IMAGE_PROJECT:-ubuntu-os-cloud}"
BOOT_DISK_SIZE="${BOOT_DISK_SIZE:-30GB}"
BOOT_DISK_TYPE="${BOOT_DISK_TYPE:-pd-balanced}"

# Service account attached to the VM so it can pull secrets from Secret
# Manager at deploy time without embedding a long-lived key file on disk.
# See deploy/secrets-setup.md for the `gcloud iam service-accounts create` +
# `secretmanager.secretAccessor` role-binding this depends on — run that
# BEFORE this script if SA_EMAIL doesn't exist yet.
SA_EMAIL="${SA_EMAIL:?Set SA_EMAIL, e.g. export SA_EMAIL=nightshift-vm@\$PROJECT_ID.iam.gserviceaccount.com (see deploy/secrets-setup.md)}"

echo "== Project: ${PROJECT_ID} | Zone: ${ZONE} | VM: ${VM_NAME} (${MACHINE_TYPE}) =="

# -----------------------------------------------------------------------
# 1. Startup script: installs Docker Engine + Compose plugin on first boot.
#    Idempotent by nature (checks `command -v docker` first) so a VM reset
#    / re-run of the startup script doesn't reinstall needlessly.
# -----------------------------------------------------------------------
STARTUP_SCRIPT_FILE="$(mktemp)"
cat > "${STARTUP_SCRIPT_FILE}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl gnupg
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  ARCH="$(dpkg --print-architecture)"
  CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
  echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  systemctl enable --now docker
fi

# `docker compose` (v2, the plugin) is what deploy/docker-compose.prod.yml's
# `deploy.resources.limits` needs to be honored outside Swarm mode — confirm
# it landed.
docker compose version
EOF

# -----------------------------------------------------------------------
# 2. Create the VM (idempotent: skip if it already exists).
# -----------------------------------------------------------------------
if gcloud compute instances describe "${VM_NAME}" --project="${PROJECT_ID}" --zone="${ZONE}" >/dev/null 2>&1; then
  echo "VM ${VM_NAME} already exists — skipping create. Edit in place with 'gcloud compute instances update' if needed."
else
  gcloud compute instances create "${VM_NAME}" \
    --project="${PROJECT_ID}" \
    --zone="${ZONE}" \
    --machine-type="${MACHINE_TYPE}" \
    --image-family="${IMAGE_FAMILY}" \
    --image-project="${IMAGE_PROJECT}" \
    --boot-disk-size="${BOOT_DISK_SIZE}" \
    --boot-disk-type="${BOOT_DISK_TYPE}" \
    --service-account="${SA_EMAIL}" \
    --scopes="cloud-platform" \
    --tags="nightshift-web" \
    --metadata-from-file=startup-script="${STARTUP_SCRIPT_FILE}"
  echo "VM created. It will take a minute or two to finish the Docker install via startup-script."
fi
rm -f "${STARTUP_SCRIPT_FILE}"

# -----------------------------------------------------------------------
# 3. Firewall: open ONLY 80/443, and ONLY to instances tagged
#    `nightshift-web` (i.e. just this VM). Everything else — Postgres
#    (5432), Redis (6379), the FastAPI app port (8000) — must NEVER be
#    reachable from the public internet. This is a hard security
#    requirement, not a convenience default:
#      - docker-compose.prod.yml already doesn't publish those ports to the
#        host at all (defense in depth #1);
#      - this firewall rule is defense in depth #2, at the network level,
#        so a future Compose edit that accidentally adds a `ports:` mapping
#        for postgres/redis/core_api still isn't reachable from outside the
#        VM.
#    Do not add rules opening 5432/6379/8000 (or any other app-internal
#    port) to 0.0.0.0/0. If you need to debug Postgres/Redis from your
#    laptop, use `gcloud compute ssh` + an SSH tunnel (IAP or otherwise),
#    never a public firewall rule.
# -----------------------------------------------------------------------
if gcloud compute firewall-rules describe nightshift-allow-web --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "Firewall rule nightshift-allow-web already exists — skipping."
else
  gcloud compute firewall-rules create nightshift-allow-web \
    --project="${PROJECT_ID}" \
    --direction=INGRESS \
    --action=ALLOW \
    --rules=tcp:80,tcp:443 \
    --source-ranges=0.0.0.0/0 \
    --target-tags=nightshift-web \
    --description="NightShift AI: public HTTP/HTTPS only. Postgres/Redis/core_api stay internal-only — see script comments."
fi

# Optional but recommended: allow SSH only via Identity-Aware Proxy's range
# rather than 0.0.0.0/0, so port 22 isn't open to the whole internet either.
if gcloud compute firewall-rules describe nightshift-allow-iap-ssh --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "Firewall rule nightshift-allow-iap-ssh already exists — skipping."
else
  gcloud compute firewall-rules create nightshift-allow-iap-ssh \
    --project="${PROJECT_ID}" \
    --direction=INGRESS \
    --action=ALLOW \
    --rules=tcp:22 \
    --source-ranges=35.235.240.0/20 \
    --description="SSH only via Identity-Aware Proxy tunnel (gcloud compute ssh --tunnel-through-iap), not the open internet."
  echo "NOTE: with this rule, SSH in via: gcloud compute ssh ${VM_NAME} --zone=${ZONE} --tunnel-through-iap"
fi

# -----------------------------------------------------------------------
# 4. Budget alert. $50/month is a SUGGESTED DEFAULT for a single e2-medium
#    plus egress/disk/Secret Manager calls — pad well above the ~$25-40/mo
#    the VM itself should cost so you're alerted on a real anomaly (a
#    runaway LLM-cost issue is app-level, guarded separately by
#    LLM_MAX_CALLS_PER_DAY in .env — this budget is a GCP-billing-level
#    backstop, not a substitute). Adjust BUDGET_AMOUNT_USD to whatever your
#    actual comfort level is; this number is not authoritative.
#
#    Requires a Billing Account ID (not the same as PROJECT_ID) —
#    find yours with: gcloud billing accounts list
#    And the Billing Budgets API enabled:
#      gcloud services enable billingbudgets.googleapis.com
# -----------------------------------------------------------------------
BILLING_ACCOUNT_ID="${BILLING_ACCOUNT_ID:-}"
BUDGET_AMOUNT_USD="${BUDGET_AMOUNT_USD:-50}"

if [ -z "${BILLING_ACCOUNT_ID}" ]; then
  echo "SKIPPING budget creation: set BILLING_ACCOUNT_ID (see 'gcloud billing accounts list') and re-run this section."
  echo "Example:"
  echo "  export BILLING_ACCOUNT_ID=XXXXXX-XXXXXX-XXXXXX"
  echo "  export BUDGET_AMOUNT_USD=50"
  echo "  gcloud billing budgets create \\"
  echo "    --billing-account=\${BILLING_ACCOUNT_ID} \\"
  echo "    --display-name='NightShift AI monthly budget' \\"
  echo "    --budget-amount=\${BUDGET_AMOUNT_USD}USD \\"
  echo "    --threshold-rule=percent=0.5 \\"
  echo "    --threshold-rule=percent=0.9 \\"
  echo "    --threshold-rule=percent=1.0 \\"
  echo "    --filter-projects=projects/\${PROJECT_ID}"
else
  # Alerts at 50%/90%/100% of BUDGET_AMOUNT_USD, scoped to just this project
  # (a billing account can cover multiple projects — --filter-projects keeps
  # this budget from also watching unrelated projects on the same account).
  # Notifications go to the Cloud Billing-configured recipients (billing
  # account admins/users) by default; wire a Pub/Sub topic + Cloud Function
  # for Slack/PagerDuty-style alerts if you want more than email — out of
  # scope for this hackathon deployment.
  gcloud billing budgets create \
    --billing-account="${BILLING_ACCOUNT_ID}" \
    --display-name="NightShift AI monthly budget" \
    --budget-amount="${BUDGET_AMOUNT_USD}USD" \
    --threshold-rule=percent=0.5 \
    --threshold-rule=percent=0.9 \
    --threshold-rule=percent=1.0 \
    --filter-projects="projects/${PROJECT_ID}" \
    || echo "Budget creation failed or a budget with this name may already exist — check 'gcloud billing budgets list --billing-account=${BILLING_ACCOUNT_ID}'."
fi

# -----------------------------------------------------------------------
# 5. Reserve a static external IP (optional but strongly recommended) so
#    the domain's DNS A record doesn't have to be updated every time the VM
#    is stopped/restarted (ephemeral IPs can change on stop/start).
# -----------------------------------------------------------------------
if gcloud compute addresses describe nightshift-ip --project="${PROJECT_ID}" --region="${REGION}" >/dev/null 2>&1; then
  echo "Static IP nightshift-ip already reserved — skipping."
else
  echo "Reserve a static IP with:"
  echo "  gcloud compute addresses create nightshift-ip --project=${PROJECT_ID} --region=${REGION}"
  echo "Then attach it to the running VM and point your domain's DNS A record at it (see deploy/README.md)."
fi

echo "== Done. Next: deploy/secrets-setup.md, then deploy/README.md's deploy steps. =="
