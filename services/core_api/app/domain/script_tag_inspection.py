"""Script Tag Inspection Engine — Sprint 4 Step 3, Tracking Specialist's
Observe step.

Pure domain logic, no framework or Shopify-client imports (mirrors
`discount_inspection.py`'s own contract exactly). Compares the store's
currently-live script tags against the set previously observed and
snapshotted, and flags any previously-known tracking script that has since
disappeared — the "script-tag pattern detection" this specialist exists to
perform (Demo Incident Generator Scenario 2: "Rogue Developer Theme Break"
deletes the Meta Pixel script tag).

Detection rule (deterministic — no LLM judgment call, mirroring
`CheckoutSpecialistAgent`'s own non-LLM rationale): if a script tag `src`
this store has previously had snapshotted is no longer present in the live
`scriptTags` listing, that is a real, structural fact — not a probabilistic
guess — so this engine reports it every time with a fixed confidence, same
as `discount_inspection.py`'s `STRUCTURAL_DETECTION_CONFIDENCE` pattern.

Deliberately out of scope this step (documented, not an oversight — mirrors
the "Defer past hackathon" framing already used for Checkout Specialist's
broader health checks in `SPRINT4_AI_WORKFORCE_VISION.md`): detecting an
*unexpected new* script tag that wasn't there before (a genuinely "rogue"
addition, as opposed to a removal). Auto-recreating a known-good script from
our own snapshot is safe; auto-flagging-for-deletion an arbitrary unknown
script an app or theme developer may have deliberately added is a
materially different, more consequential judgment call this step does not
attempt.

`KNOWN_TRACKING_PATTERNS` is a best-effort, human-readable label only — it
never gates detection (a missing script tag is flagged whether or not its
`src` matches a known pattern), it only makes findings/audit text
recognizable ("Meta Pixel" instead of a bare URL).
"""

from __future__ import annotations

from dataclasses import dataclass

KNOWN_TRACKING_PATTERNS: dict[str, str] = {
    "connect.facebook.net": "Meta Pixel",
    "googletagmanager.com": "Google Tag Manager",
    "google-analytics.com": "Google Analytics",
    "analytics.tiktok.com": "TikTok Pixel",
}


def identify_pattern(src: str) -> str | None:
    for substring, label in KNOWN_TRACKING_PATTERNS.items():
        if substring in src:
            return label
    return None


@dataclass(frozen=True)
class TrackingSnapshotEntry:
    src: str
    display_scope: str | None
    pattern_name: str | None


@dataclass(frozen=True)
class ScriptTagFinding:
    title: str
    severity: str
    description: str
    affected_resources: list[str]  # [src, display_scope, pattern_name] — flat triple, one per finding
    evidence: dict


@dataclass(frozen=True)
class ScriptTagInspectionReport:
    live_script_tags_scanned: int
    findings: list[ScriptTagFinding]
    newly_snapshotted: list[TrackingSnapshotEntry]


def inspect_script_tags(
    *,
    live_script_tags: list[dict],
    known_snapshots: dict[str, TrackingSnapshotEntry],
) -> ScriptTagInspectionReport:
    """`live_script_tags` is the normalized shape
    `ShopifyGraphQLClient.fetch_script_tags` returns:
    `{src, display_scope, cache}` (no `id` needed for detection — a removed
    tag has no live id to reference anymore; recreation uses the snapshot's
    own `src`/`display_scope`, not any stale id). `known_snapshots` maps
    `src` -> the last snapshotted `TrackingSnapshotEntry` for this store.

    A `src` never seen before becomes a new snapshot entry (first-observation
    seeding, same convention as `theme_inspection.py`) rather than a finding
    — there's nothing to compare a first sighting against.
    """
    live_srcs = {tag["src"] for tag in live_script_tags if tag.get("src")}
    findings: list[ScriptTagFinding] = []
    newly_snapshotted: list[TrackingSnapshotEntry] = []

    for tag in live_script_tags:
        src = tag.get("src")
        if not src or src in known_snapshots:
            continue
        newly_snapshotted.append(
            TrackingSnapshotEntry(
                src=src,
                display_scope=tag.get("display_scope"),
                pattern_name=identify_pattern(src),
            )
        )

    for src, snapshot in known_snapshots.items():
        if src in live_srcs:
            continue
        label = snapshot.pattern_name or "tracking script"
        findings.append(
            ScriptTagFinding(
                title=f"{label} script tag removed from storefront",
                severity="HIGH",
                description=(
                    f"The '{label}' script tag (src: {src}) was previously active on this "
                    "storefront and is no longer present. Any tracking, analytics, or "
                    "conversion pixel this script provided has stopped firing."
                ),
                affected_resources=[src, snapshot.display_scope or "ONLINE_STORE", snapshot.pattern_name or ""],
                evidence={
                    "check": "tracking_script_removed",
                    "src": src,
                    "pattern_name": snapshot.pattern_name,
                    # Sprint 4: lets the worker task reuse an existing open
                    # issue/approval for this exact script when a later,
                    # still-unfixed shift re-observes the same removal,
                    # instead of creating a duplicate. Scoped to `src`
                    # specifically, not a global singleton like discount's
                    # key — two DIFFERENT scripts removed are legitimately
                    # separate issues.
                    "dedup_key": f"tracking:{src}",
                },
            )
        )

    return ScriptTagInspectionReport(
        live_script_tags_scanned=len(live_script_tags),
        findings=findings,
        newly_snapshotted=newly_snapshotted,
    )
