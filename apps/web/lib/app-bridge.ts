/**
 * Shopify App Bridge v3 initialization (Sprint 1 Feature 4).
 *
 * Detects whether the app is running inside the Shopify Admin iframe. If
 * not, the UI shows an "Open in Shopify Admin" deep link instead of a blank
 * or broken embedded shell (Section 1.10 edge case).
 */

import createApp, { type ClientApplication } from "@shopify/app-bridge";
import { getSessionToken as getAppBridgeSessionToken } from "@shopify/app-bridge-utils";

export interface AppBridgeConfig {
  apiKey: string;
  host: string;
}

// `ClientApplication<S extends AppBridgeState = AppBridgeState>` — its own
// default type param is already the real `AppBridgeState` shape `createApp()`
// returns. An earlier version of this file explicitly pinned this to
// `ClientApplication<Record<string, unknown>>` as a placeholder, but
// `Record<string, unknown>` doesn't satisfy the `extends AppBridgeState`
// constraint, which is what caused the type errors — using the bare,
// default-parameterized type is both correct and simpler, and this file
// never reads `staffMember`/`context`/`features` off the instance anyway.
let appBridgeInstance: ClientApplication | null = null;

export function isEmbeddedInShopifyAdmin(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.top !== window.self && new URLSearchParams(window.location.search).has("host");
  } catch {
    // Cross-origin access to window.top throws when embedded — that IS the
    // embedded case, so treat it as embedded.
    return true;
  }
}

export function getShopifyAdminDeepLink(shopParam: string | null): string {
  if (!shopParam) return "https://admin.shopify.com";
  return `https://${shopParam}/admin/apps/nightshift-ai`;
}

export function initializeAppBridge(config: AppBridgeConfig): ClientApplication {
  if (appBridgeInstance) return appBridgeInstance;
  appBridgeInstance = createApp({
    apiKey: config.apiKey,
    host: config.host,
    forceRedirect: true,
  });
  return appBridgeInstance;
}

export async function getSessionToken(): Promise<string> {
  if (!appBridgeInstance) {
    throw new Error("App Bridge has not been initialized yet");
  }
  return getAppBridgeSessionToken(appBridgeInstance);
}
