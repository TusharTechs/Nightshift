#!/bin/sh
# Cloud Run entrypoint for the `worker` service (docker/Dockerfile.workers'
# image, deployed with this script as a --command override — no separate
# Dockerfile needed).
#
# Why this exists: Cloud Run requires every service to listen on $PORT and
# respond to health checks, but a Celery worker is a pure background queue
# consumer with nothing to listen on. This starts a trivial HTTP health
# responder in the background (just enough for Cloud Run's own startup/
# liveness probe — it proves nothing about Celery's actual health, which is
# why docker/Dockerfile.workers' own `celery inspect ping` HEALTHCHECK
# remains the real liveness signal for docker-compose deployments), then
# `exec`s the real celery worker process as PID 1's replacement in the
# foreground — so `docker stop`/Cloud Run's SIGTERM reaches Celery directly
# for a clean shutdown, not a wrapper script.
set -e

python3 -c "
import http.server, os
port = int(os.environ.get('PORT', 8080))
class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *a):
        pass  # Cloud Run's own request logging already captures this; skip the duplicate.
http.server.HTTPServer(('0.0.0.0', port), Health).serve_forever()
" &

exec celery -A app.infrastructure.messaging.celery_app worker \
    -Q celery:observation,celery:reasoning,celery:execution,celery:verification,celery:cron \
    -l info
