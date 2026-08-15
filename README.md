# NightShift AI

**An autonomous AI workforce that runs Shopify store operations overnight.**

Four specialist AI agents watch a merchant's store while they sleep — catalog quality, discount pricing, theme integrity, and tracking pixels. Each finding goes through the same trust pipeline: detect, reason about the risk, either fix it automatically (when genuinely safe) or ask the merchant first, then verify the fix actually worked. Every morning, Chief Ops AI — powered by Gemini — reads everything every specialist found and writes the one paragraph a merchant actually needs to read.

Nothing here is invented: every dollar figure, every timestamp, every claim traces back to something Shopify's own API returned or a rule the code can point to. Where the data doesn't support a claim, NightShift says so instead of guessing.

## Built on Google Cloud

- **Gemini, via Vertex AI** — Chief Ops AI's Executive Briefing synthesis (`app/domain/chief_ops.py`) is hard-wired to Gemini 3.6 Flash, called through Vertex AI so usage bills through the project's normal Google Cloud account rather than a separate API-key balance.
- **Google GenAI SDK** — `google-genai`, Google's actively-maintained agent/GenAI SDK, is the only client library used for every Gemini call in this codebase (`app/infrastructure/llm/gemini_client.py`).
- **Cloud Run** — `core_api` (the FastAPI app) and the Celery `worker` both deploy as Cloud Run services; Celery Beat's role is filled by **Cloud Scheduler** calling a dedicated internal endpoint, since Cloud Run has no persistent-process equivalent of Beat. See `deploy/` and `PRODUCTION_READINESS.md`.

## How it works

```
inspect_catalog → inspect_discounts → inspect_theme_files → inspect_tracking_scripts
      → plan_cognitive_tasks (risk assessment, approval routing, auto-execution)
      → compile_shift_report (Chief Ops AI synthesis, health score, executive summary)
```

Every issue any specialist finds moves through the same eight-step lifecycle regardless of which specialist found it: **Observe → Reason → Assess Risk → Approve (if required) → Execute → Verify → Explain → Persist.** A fix is only ever auto-executed when its risk classification says it's genuinely safe; anything else waits in the Approval Center for a human decision. Every fix is re-verified against Shopify's live data afterward — the system never just trusts that an API call "succeeded."

### The specialists

| Specialist | Watches | Detects | Fix |
|---|---|---|---|
| **Product Quality** | Product catalog | Missing images/ALT text, thin descriptions, zero-inventory variants | Generates ALT text (autonomous); rewrites descriptions (approval-gated) |
| **Checkout Specialist** | Active discount codes | Overlapping, stackable storewide discounts | Deactivates duplicates (approval-gated), fully reversible |
| **Theme Guardian** | A watch-list of critical theme files | Live divergence from the last known-good snapshot | Attempts a real, automated restore where Shopify's write exemption allows it; otherwise a guided fix with a real, honest diff — never fabricated |
| **Tracking Specialist** | Installed script tags | A previously-seen tracking script disappearing | Recreates it from its own snapshot (approval-gated), fully reversible |

### Beyond detection

- **Chief Ops AI** — once 1+ specialists report findings in a shift, one Gemini call synthesizes them into an executive-readable narrative, flagging a real, stated root cause when findings are genuinely correlated.
- **Ask NightShift** — a merchant's free-text question, grounded only in the store's own recent shift history; the model says "I don't know" rather than invent an answer.
- **Approval Center** — every fix awaiting a decision, with its risk level, confidence score, and (for Theme Guardian) a real side-by-side diff of the actual file content.
- **Work Log / Shift Replay** — an append-only audit trail of literally everything the AI or a merchant has done, replayable as an animated timeline.
- **Counterfactual ROI** — "what if NightShift wasn't here" cards using only real, already-computed numbers.
- **Billing** — real Shopify Billing API integration (Free auto-provisioned at install; Pro/Business through Shopify's own confirmation page). Free-tier stores are excluded from automatic nightly monitoring — the one enforcement point.

## Tech stack

- **Backend**: FastAPI (async), Celery + Redis, PostgreSQL, SQLAlchemy 2.0, Alembic
- **AI**: Google GenAI SDK (Gemini, via Vertex AI) for both Chief Ops AI and every per-specialist detection agent
- **Frontend**: Next.js, embedded in Shopify Admin via App Bridge
- **Infra**: Docker Compose (local), Cloud Run + Cloud Scheduler (production) — see `deploy/`

## Local development

```bash
pnpm install
docker compose -f docker/docker-compose.yml up -d
cd services/core_api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
```

Copy `.env.example` to `.env` and fill in your own Shopify Partner app credentials, LLM API keys, and (optionally) `GCP_PROJECT_ID` to route Gemini calls through Vertex AI instead of the Gemini Developer API.

Run the worker (separate terminal, from `services/core_api`):

```bash
PYTHONPATH=..:. celery -A app.infrastructure.messaging.celery_app worker \
  -Q celery:observation,celery:reasoning,celery:execution,celery:verification,celery:cron -l info
```

Run the app itself via the Shopify CLI from the repo root:

```bash
npm install -g @shopify/cli
shopify app config link   # links to your own Partner Dashboard app
shopify app dev
```

### Corporate network TLS certs (only if you hit SSL errors)

If you're behind a network that intercepts outbound TLS with its own root CA, outbound calls to Shopify/Google/PyPI will fail with `CERTIFICATE_VERIFY_FAILED`. Generate a combined CA bundle once:

```bash
cd services/core_api && source .venv/bin/activate
security find-certificate -a -p /Library/Keychains/System.keychain > /tmp/mac-roots.pem
security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain >> /tmp/mac-roots.pem
cat "$(python3 -c 'import certifi; print(certifi.where())')" /tmp/mac-roots.pem > ~/.nightshift-ai-ca-bundle.pem
```

Then point Node and Docker builds at it (both Dockerfiles pick up `docker/ca-bundle.pem` automatically if present, and no-op if it's absent — e.g. on a real cloud build):

```bash
cp ~/.nightshift-ai-ca-bundle.pem docker/ca-bundle.pem
echo 'export NODE_EXTRA_CA_CERTS="$HOME/.nightshift-ai-ca-bundle.pem"' >> ~/.zshrc
```

## Tests

```bash
cd services/core_api
pytest tests/unit tests/integration
```

363 backend tests, covering every specialist agent, every API endpoint, and every safety mechanism (rollback, verification, budget guards, HMAC-verified webhooks).

## Deployment

See `deploy/README.md` for the Cloud Run + Cloud Scheduler production topology, and `PRODUCTION_READINESS.md` for the full checklist, security posture, and Theme Guardian's write-capability matrix.

## Project documentation

- `PRODUCTION_READINESS.md` — deployment runbook, security/env-var checklists.
- `CONFLICTS.md` / `DECISIONS.md` — the engineering rationale log: every place a source spec was ambiguous, contradictory, or had to be adapted to a real platform constraint, resolved and recorded rather than silently guessed.
- `HACKATHON_DISCLOSURE.md` — the honest revenue/user/expense disclosure for this project's hackathon submission.
