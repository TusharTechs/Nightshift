# NightShift AI — Production Deployment Runbook

Single Google Cloud Compute Engine VM, Docker Compose, Caddy for automatic
TLS. This satisfies the hackathon's "must use at least one Google Cloud
product" requirement via Compute Engine, while minimizing recurring cost —
no Cloud SQL, no Memorystore, no GKE; Postgres and Redis run as plain
containers on the same VM as the app.

## 0. Prerequisites

- A GCP project with billing enabled, and the `gcloud` CLI authenticated
  locally (`gcloud auth login`, `gcloud config set project YOUR_PROJECT_ID`).
- A domain name you control, able to add an A record (e.g. `app.nightshift.ai`).
- Shopify Partner Dashboard access for this app (client ID `754d95090400cda2577808aed745018f`
  per `shopify.app.toml`) to update the App URL / redirect URLs once you have
  a real domain.
- Compute Engine + Secret Manager + Billing Budgets APIs enabled:
  ```bash
  gcloud services enable compute.googleapis.com secretmanager.googleapis.com billingbudgets.googleapis.com
  ```

## 1. VM sizing (already decided — recap)

**e2-medium**: 2 vCPU (shared core), 4 GB RAM. On-demand pricing is roughly
**$24-30/month** for the VM itself in most US regions as of 2026 (e.g.
~$0.0335/hr ≈ $24.50/mo in `us-central1`; add a small amount for the boot
disk — a 30 GB `pd-balanced` disk is a few dollars/month more — so budget
**~$30-40/month total** for compute + disk before any LLM API spend).
Committed-use discounts (1yr/3yr) reduce this further. **Check
https://cloud.google.com/compute/all-pricing for current numbers before
committing budget** — the figures above are ballpark, not quoted pricing.

Why e2-medium over e2-small (2 vCPU shared, 2 GB RAM, roughly half the
price): this VM runs five-plus long-lived processes concurrently — Postgres,
Redis, the FastAPI process, two Celery processes (worker + beat), and Caddy
— plus the OS and Docker daemon. 2 GB is tight enough that Postgres's
buffers plus two Python process's RSS risk the OOM killer under any real
load or a catalog-scan burst; 4 GB gives real headroom. If cost pressure is
severe, e2-small can technically run this stack at idle, but is not
recommended once real merchant traffic or LLM-call bursts start.

## 2. Run order

1. **`deploy/secrets-setup.md`** — create the VM's service account, grant it
   `secretmanager.secretAccessor`, create the Secret Manager secrets. Do this
   first — `gcp-vm-setup.sh` attaches the service account it creates here.
2. **`deploy/gcp-vm-setup.sh`** — read it top to bottom, export the required
   env vars (`PROJECT_ID`, `SA_EMAIL`, optionally `BILLING_ACCOUNT_ID` for
   the budget alert), then run it section by section. It creates the VM,
   opens firewall rules for **80/443 only** (see the security note below),
   and sets up a budget alert.
3. Point your domain's DNS A record at the VM's external IP (reserve a
   static IP first — the script prints the command — so this doesn't need
   to be redone on every VM restart).
4. SSH into the VM (`gcloud compute ssh nightshift-ai-prod --zone=... --tunnel-through-iap`
   if you used the IAP-only SSH firewall rule from the setup script) and
   clone the repo:
   ```bash
   sudo mkdir -p /opt/nightshift-ai && sudo chown "$USER" /opt/nightshift-ai
   git clone <your-repo-url> /opt/nightshift-ai
   cd /opt/nightshift-ai
   ```
5. Materialize `.env` from Secret Manager (see `deploy/secrets-setup.md`
   section 3 — either run `deploy/fetch-secrets.sh` by hand or install it as
   the systemd `ExecStartPre` shown there). Fill in `CADDY_DOMAIN` with your
   real domain.
6. Build and start everything:
   ```bash
   docker compose -f deploy/docker-compose.prod.yml --env-file .env up -d --build
   ```
   (Requires the Compose **V2** plugin — `docker compose`, not the legacy
   `docker-compose` — for the `deploy.resources.limits` in
   `docker-compose.prod.yml` to actually cap CPU/memory outside Swarm mode.
   The startup-script in `gcp-vm-setup.sh` installs this plugin.)

## 3. First-run database migration

`alembic upgrade head` needs to run once against the fresh production
Postgres container before `core_api`/`worker`/`beat` can do anything useful
(the Sprint 1-5 migrations create every table, including the `vector`
extension `store_memories` needs). Run it from inside the running
`core_api` container so it uses the exact same dependency versions and
`DATABASE_URL` the app itself uses:

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file .env exec core_api \
  alembic upgrade head
```

If `core_api` is crash-looping because the schema doesn't exist yet, run it
against a one-off container instead:

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file .env run --rm core_api \
  alembic upgrade head
```

Verify: `docker compose -f deploy/docker-compose.prod.yml --env-file .env exec core_api alembic current`
should print the latest revision (`0005_...` as of this snapshot).

## 4. Point Shopify at the new domain

In the [Shopify Partner Dashboard](https://partners.shopify.com/) → your app
(client ID `754d95090400cda2577808aed745018f`) → App setup:

- **App URL**: `https://<your-domain>` (e.g. `https://app.nightshift.ai`)
- **Allowed redirection URL(s)**: `https://<your-domain>/api/v1/auth/shopify/callback`
  (confirmed against `services/core_api/app/api/v1/auth.py`: router prefix
  `/api/v1/auth` + `@router.get("/shopify/callback")` — re-check this if
  that file changes)

Also update `shopify.app.toml`'s `application_url` and `redirect_urls`
locally and redeploy the config (`shopify app deploy`) if you want the CLI's
config-tracking to match reality, since `include_config_on_deploy = true` is
set.

Make sure `CADDY_DOMAIN` in the VM's `.env` matches this domain exactly —
Caddy's automatic TLS is keyed off it.

## 5. Operating the deployment

**Tail logs:**
```bash
docker compose -f deploy/docker-compose.prod.yml --env-file .env logs -f
# or a single service:
docker compose -f deploy/docker-compose.prod.yml --env-file .env logs -f core_api
```

**Redeploy on a code change:**
```bash
cd /opt/nightshift-ai
git pull
docker compose -f deploy/docker-compose.prod.yml --env-file .env up -d --build
# if the change includes a new migration:
docker compose -f deploy/docker-compose.prod.yml --env-file .env exec core_api alembic upgrade head
```

**Rollback:** tag/keep the previous working image before rebuilding (e.g.
`docker tag nightshift-ai-core_api:latest nightshift-ai-core_api:rollback`
before `--build`, or simply `git checkout <previous-commit>` and rebuild
from there — Compose's `build:` context means the image is whatever the
current working tree produces, there's no separate registry step in this
setup). To roll back:
```bash
git checkout <previous-good-commit>
docker compose -f deploy/docker-compose.prod.yml --env-file .env up -d --build
```
If a migration shipped with the bad deploy needs reverting too, check the
corresponding `alembic downgrade <revision>` before rolling the app back —
don't roll back code against a schema that's moved forward, or vice versa.

## 6. Security requirements (non-negotiable)

- **Only 80/443 are open to the public internet.** Postgres (5432), Redis
  (6379), and the FastAPI app port (8000) are never published to the host
  in `docker-compose.prod.yml` and are additionally blocked by the VM's
  firewall rules — this is defense in depth, not redundant. Do not add a
  `ports:` mapping for postgres/redis/core_api, and do not open a firewall
  rule for those ports, ever. Debug them via SSH tunnel
  (`gcloud compute ssh --tunnel-through-iap` + `docker compose exec`), not
  by exposing them.
- `.env` on the VM holds live secrets (`chmod 600`, never committed — see
  the repo's `.gitignore`, which now excludes `.env`/`.env.*`).
- SSH access is IAP-tunnel-only if you used the firewall rule from
  `gcp-vm-setup.sh` (`nightshift-allow-iap-ssh`), not open to `0.0.0.0/0`.

## 7. Observability

There is no error-reporting SDK in the codebase today (no Sentry, no
equivalent). Rather than editing `app/main.py` / `app/config.py` for this
deployment task — **`app/config.py` currently has an uncommitted,
in-progress change from other work in this repo** (`chief_ops_llm_provider`),
so touching the same file here risked a conflict — Sentry wiring is
documented here as a manual post-deploy step instead:

1. Add to `services/core_api/requirements.txt`:
   ```
   sentry-sdk[fastapi]==2.19.2
   ```
   (check https://pypi.org/project/sentry-sdk/ for the current release before
   pinning — this is a suggested version, not verified against this repo's
   other pins).
2. Add a `sentry_dsn` setting to `Settings` in `app/config.py`:
   ```python
   sentry_dsn: str = Field(default="", validation_alias=AliasChoices("SENTRY_DSN"))
   ```
3. Near the top of `create_app()` in `app/main.py`, before the router
   includes:
   ```python
   if settings.sentry_dsn:
       import sentry_sdk
       sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment, traces_sample_rate=0.1)
   ```
4. Add `SENTRY_DSN=` (empty by default) to `.env.example` and to the VM's
   `.env` (via a new Secret Manager secret if you want the real DSN kept out
   of plaintext, or just a plain value — a Sentry DSN is not highly
   sensitive, but treat it consistently with the rest of `.env`).

Left empty, `SENTRY_DSN` is a no-op — this never breaks a deploy that
doesn't have a Sentry account configured. Apply steps 2-3 yourself once
`app/config.py`'s in-flight change lands, to avoid a merge conflict with
concurrent work.

In the meantime, `docker compose logs -f` (per-service, see above) plus each
container's `HEALTHCHECK` (`docker compose ps` shows health status) are the
available signals.

## 8. Known assumptions to double-check before a real deploy

- **`docker compose` (V2 plugin) resource limit enforcement outside Swarm
  mode**: assumed available since Compose V2; confirm on the actual VM
  (`docker compose version` — the startup script in `gcp-vm-setup.sh`
  installs `docker-compose-plugin`, which is V2).
- **e2-medium real-world sizing** in `docker-compose.prod.yml` is a starting
  allocation (postgres 1 vCPU/1GB, core_api 1 vCPU/896MB, worker 1 vCPU/768MB,
  redis 0.5 vCPU/256MB, beat 0.25 vCPU/256MB, caddy 0.25 vCPU/128MB) sized to
  fit under 4GB with headroom for the OS — watch `docker stats` under real
  load and adjust; nothing about actual NightShift AI traffic patterns was
  load-tested here.
- **$50/month budget default** in `gcp-vm-setup.sh` is a suggested
  placeholder, not a business decision — set `BUDGET_AMOUNT_USD` to whatever
  the real comfort level is.
- **GCP pricing figures** throughout this runbook are ballpark (verified via
  web search at time of writing, August 2026) — check
  https://cloud.google.com/compute/all-pricing before committing budget.
- **Standard Ubuntu 22.04 + startup-script** was chosen over Container-
  Optimized OS / `create-with-container` for docker-compose reliability —
  re-evaluate if your org has a hard COS-only policy.
