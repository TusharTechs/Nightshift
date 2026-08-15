# NightShift AI — Devpost Submission: Financial & User Disclosure

**Important, read this first:** every figure below reflects the actual, true state of this project as I understand it from building and testing it with you. I have not invented any revenue, users, or testimonials, and you shouldn't either when you submit — the rules explicitly allow (and expect) a true $0 pre-revenue disclosure; fabricating any of this would be a real problem if the submission is reviewed or audited, and undermines rather than helps your case. A few line items below need YOUR real numbers (things I don't have access to, like your actual Google Cloud billing dashboard) — marked clearly where you need to fill them in.

---

## 1. Total Revenue

**$0.** NightShift AI has not been submitted to the Shopify App Store and is running as a custom/development app against a single Shopify development store. It has never processed a real merchant subscription or charge — confirmed directly: attempting a live `POST /api/v1/billing/subscribe` call returns Shopify's own platform error, *"Apps without a public distribution cannot use the Billing API."* No arms-length third party has ever paid for this product.

## 2. Revenue by Month

| Month | Revenue (USD) |
|---|---|
| May 2026 | $0 |
| June 2026 | $0 |
| July 2026 | $0 |
| August 2026 | $0 |

## 3. Total Expenses

Real cost categories incurred building this project, during the Hackathon period:

- **LLM API usage**: Google Gemini (via the Gemini Developer API / Vertex AI) for both the per-specialist detection agents' reasoning calls and Chief Ops AI's cross-agent synthesis — metered, pay-per-token.
- **Google Cloud infrastructure**: Cloud Run hosting + Vertex AI inference calls (see hosting migration notes — you have $150 in GCP credit currently covering this).
- **No contractor fees** — solo-built, with AI-assisted development.
- **No paid hosting before this** — local development used Docker Compose on the developer's own machine; cloud hosting is new as of this submission cycle.

> **⚠️ You need to fill in the actual dollar total.** I don't have access to your Google Cloud Billing dashboard — please pull the real numbers from:
> - Google Cloud Console → Billing → Reports (filtered to this project)
> - Google AI Studio → Usage & billing (if any Gemini Developer API spend occurred before the Vertex AI migration)
>
> Report the true total. If it's a small number (this is a lean hackathon project, not a funded startup), that's fine and expected — don't round up or estimate generously.

## 4. Marketing and Customer Acquisition Spend

**$0.** No paid advertising, no paid customer acquisition campaigns, no influencer/affiliate spend. This project has not been marketed to any customer beyond the developer's own testing.

## 5. User Evidence

**Real user count: 0 external/arms-length users.**

The only "user" of NightShift AI to date is the developer (Tushar Agarwal), operating a single Shopify development store (`nightshift-ai-test-store.myshopify.com`) for the purpose of building and testing the product. No other merchant, business, or individual has installed, used, or interacted with this app.

**No testimonials or customer feedback exist**, because no customers exist yet. Do not include any in the submission — inventing quotes or feedback would misrepresent the product's actual traction.

If judges ask for user evidence, the honest answer is: *"This is a pre-launch hackathon project. It has been built and validated through extensive automated testing (359 passing backend tests) and manual end-to-end testing against a real Shopify development store, but has not yet been used by any real third-party merchant."*

## 6. Related-Party Revenue

**$0.** Since total revenue is $0, related-party revenue is necessarily $0 too — there is no revenue of any kind, from team members, family, related entities, or otherwise, to report separately.

## 7. Evidence of Product Running

What you *can* honestly show here, all real and verifiable:

- **Automated test suite**: 359 backend tests passing, covering every agent, API endpoint, and safety mechanism (rollback, verification, budget guards, HMAC-verified webhooks).
- **Real audit trail**: the Work Log / `audit_logs` table contains genuine, timestamped records of real Shopify GraphQL mutations executed against the dev store — product description rewrites, ALT text fixes, discount deactivations, script tag recreation — each with a real Shopify API response, not a mock.
- **Real LLM call logs**: structured log lines (e.g. `chief_ops_briefing_llm_provider provider=GEMINI model=gemini-3.6-flash`) proving genuine Gemini API round-trips, not canned text.
- **Continuous scheduler operation**: a Celery Beat-driven nightly (configurable interval) scheduler has been running and dispatching real inspection shifts against the dev store since installation.
- **Screenshots/recording**: the demo video itself, plus optionally a screenshot of the Work Log, Approval Center, and Cloud Run/Vertex AI dashboards once hosting is live.

This is genuine pre-production validation evidence — it is **not** evidence of production traffic from real merchants, and the submission shouldn't imply otherwise.

## 8. Corporate ID

> **Question for you:** are you submitting as an individual, or through a registered business entity? If an entity, you'll need to provide its corporate ID/registration number per the rules. If you're submitting as an individual (no registered company), this section doesn't apply — say so plainly rather than leaving it blank/ambiguous.

---

## A note on "hope for the best"

You said this is fine to submit with zero real earnings — it is. Hackathon judging criteria for a technical/AI category typically weight the *build* (does it genuinely work, is the Google stack usage real, is the architecture sound) much more heavily than early revenue for a pre-launch project. What actually matters for the "Business Viability" criterion at this stage is a credible plan (you have one: Free/Pro/Business tiers via Shopify's real Billing API, already implemented in code even though it can't fully activate until the app goes through public distribution) plus honest evidence the product genuinely works — which you do have, in the test suite and audit logs. Padding the numbers would only create risk (judges can ask for contact info, live demos, and further financial documentation per the rules) without meaningfully helping your score.
