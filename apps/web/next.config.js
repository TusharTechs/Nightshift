/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Embedded Shopify Admin App Bridge shell — allow framing from Shopify Admin.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          {
            key: "Content-Security-Policy",
            value: "frame-ancestors https://*.myshopify.com https://admin.shopify.com;",
          },
        ],
      },
    ];
  },
  // Under `shopify app dev` (shopify.web.toml, roles=["frontend"]), Shopify
  // CLI injects BACKEND_PORT with services/core_api's local port. Proxying
  // here means the browser only ever talks to this frontend's single public
  // tunnel — no second tunnel/public URL is needed for the backend, and
  // lib/api.ts's relative fetch("/api/v1/...") resolves correctly with
  // NEXT_PUBLIC_API_BASE_URL left blank.
  async rewrites() {
    const backendPort = process.env.BACKEND_PORT;
    if (!backendPort) return [];
    return [
      {
        source: "/api/v1/:path*",
        destination: `http://localhost:${backendPort}/api/v1/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
