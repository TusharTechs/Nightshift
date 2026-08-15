import { Providers } from "../providers";

/**
 * Embedded Shopify App Bridge shell (Sprint 1 Feature 4 / Section 2.2).
 * App Bridge itself is initialized client-side in page.tsx once `host` is
 * known from the query string — App Bridge v3's createApp call requires
 * `window`, so it cannot run in a server component.
 */
export default function EmbeddedLayout({ children }: { children: React.ReactNode }) {
  return (
    <Providers>
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">{children}</div>
    </Providers>
  );
}
