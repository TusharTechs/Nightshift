#!/usr/bin/env bash
# Same fix as services/core_api/dev.sh: Shopify CLI spawns `commands.dev`
# without a shell, so `next dev -p $PORT` never expanded $PORT — Next.js
# received the literal string "$PORT" as its -p argument. Routing through
# an actual bash script fixes it, since $PORT is still a real inherited
# environment variable, just one that needs a shell to interpolate.
set -euo pipefail
cd "$(dirname "$0")"

if [ -z "${PORT:-}" ]; then
  echo "dev.sh: PORT was not set in the environment (expected from Shopify CLI)" >&2
  exit 1
fi

exec pnpm exec next dev -p "$PORT"
