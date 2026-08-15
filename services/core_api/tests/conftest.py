import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# The sandbox test runner sets ambient proxy env vars (HTTP_PROXY/ALL_PROXY,
# some pointing at a SOCKS proxy) for its own network egress control. httpx
# respects these via trust_env by default, which breaks respx-mocked HTTP
# calls in this suite (socksio isn't installed, and there's no real proxy for
# outbound Shopify calls to route through in tests anyway). This is a test
# environment concern only — production `httpx.AsyncClient` instances in
# app/infrastructure/shopify_client.py deliberately keep trust_env at its
# default so real deployments still honor any configured egress proxy.
for _proxy_var in (
    "HTTP_PROXY", "http_proxy",
    "HTTPS_PROXY", "https_proxy",
    "ALL_PROXY", "all_proxy",
):
    os.environ.pop(_proxy_var, None)

os.environ.setdefault("SHOPIFY_APP_CLIENT_ID", "test-client-id")
os.environ.setdefault("SHOPIFY_APP_SECRET", "test-shopify-app-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CELERY_BROKER_URL", "redis://localhost:6379/1")
os.environ.setdefault("CELERY_RESULT_BACKEND", "redis://localhost:6379/2")
os.environ.setdefault("NIGHTSHIFT_LOCAL_DATA_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
