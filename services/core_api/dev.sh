#!/usr/bin/env bash
# Shopify CLI spawns `commands.dev` directly (no shell), which is why
# `$PORT` was never expanded and bare `uvicorn` couldn't be found on PATH —
# your venv's activation never happens in that process. Routing through this
# script fixes both: bash here really does expand $PORT (inherited as a real
# env var regardless of how this script itself was launched), and we resolve
# uvicorn ourselves instead of relying on an activated venv being on PATH.
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${PORT:-}" ]; then
  echo "dev.sh: PORT was not set in the environment (expected from Shopify CLI)" >&2
  exit 1
fi

if [ -x ".venv/bin/uvicorn" ]; then
  UVICORN=".venv/bin/uvicorn"
elif command -v uvicorn >/dev/null 2>&1; then
  UVICORN="uvicorn"
else
  echo "dev.sh: no uvicorn found. Run this first:" >&2
  echo "  cd services/core_api && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

# Corporate networks here intercept outbound TLS with a self-signed proxy
# cert — the same root cause behind `shopify app config link`'s
# "self-signed certificate in certificate chain" error (fixed there via
# NODE_EXTRA_CA_CERTS) and the pydantic-core pip build failure. httpx's
# outbound calls to Shopify's real API (token exchange, GraphQL) hit the
# identical failure unless Python trusts that same corporate root CA.
# httpx honors SSL_CERT_FILE for its default TLS verification whenever it
# isn't already set (see httpx._config.get_ca_bundle_from_env) — this just
# wires up the combined CA bundle if one has been generated (see README for
# the one-time setup command), and is a no-op otherwise.
if [ -z "${SSL_CERT_FILE:-}" ] && [ -f "$HOME/.nightshift-ai-ca-bundle.pem" ]; then
  export SSL_CERT_FILE="$HOME/.nightshift-ai-ca-bundle.pem"
fi

exec "$UVICORN" app.main:app --reload --host 0.0.0.0 --port "$PORT"
