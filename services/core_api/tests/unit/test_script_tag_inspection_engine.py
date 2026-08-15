"""Unit tests for the Script Tag Inspection Engine (Sprint 4 Step 3 —
Tracking Specialist's Observe step). Pure domain logic, no I/O.
"""

from __future__ import annotations

from app.domain.script_tag_inspection import (
    KNOWN_TRACKING_PATTERNS,
    TrackingSnapshotEntry,
    identify_pattern,
    inspect_script_tags,
)

META_PIXEL_SRC = "https://connect.facebook.net/en_US/fbevents.js"


def test_identify_pattern_recognizes_known_substrings():
    assert identify_pattern(META_PIXEL_SRC) == "Meta Pixel"
    assert identify_pattern("https://unknown-vendor.example.com/tag.js") is None


def test_first_observation_of_a_script_tag_is_snapshotted_not_flagged():
    live = [{"src": META_PIXEL_SRC, "display_scope": "ONLINE_STORE", "cache": True}]
    report = inspect_script_tags(live_script_tags=live, known_snapshots={})

    assert report.findings == []
    assert len(report.newly_snapshotted) == 1
    assert report.newly_snapshotted[0].src == META_PIXEL_SRC
    assert report.newly_snapshotted[0].pattern_name == "Meta Pixel"


def test_missing_previously_known_script_tag_produces_a_high_severity_finding():
    known = {
        META_PIXEL_SRC: TrackingSnapshotEntry(
            src=META_PIXEL_SRC, display_scope="ONLINE_STORE", pattern_name="Meta Pixel"
        )
    }
    report = inspect_script_tags(live_script_tags=[], known_snapshots=known)

    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.severity == "HIGH"
    assert "Meta Pixel" in finding.title
    assert finding.affected_resources == [META_PIXEL_SRC, "ONLINE_STORE", "Meta Pixel"]
    assert finding.evidence["check"] == "tracking_script_removed"
    assert finding.evidence["src"] == META_PIXEL_SRC
    assert finding.evidence["dedup_key"] == f"tracking:{META_PIXEL_SRC}"


def test_still_present_known_script_tag_produces_no_finding():
    known = {
        META_PIXEL_SRC: TrackingSnapshotEntry(
            src=META_PIXEL_SRC, display_scope="ONLINE_STORE", pattern_name="Meta Pixel"
        )
    }
    live = [{"src": META_PIXEL_SRC, "display_scope": "ONLINE_STORE", "cache": True}]
    report = inspect_script_tags(live_script_tags=live, known_snapshots=known)
    assert report.findings == []
    assert report.newly_snapshotted == []


def test_known_tracking_patterns_registry_is_display_only_never_gates_detection():
    # An unrecognized src still gets flagged as removed if it was previously
    # snapshotted — the pattern registry only affects the human-readable
    # label, never whether detection fires.
    unknown_src = "https://obscure-vendor.example.com/pixel.js"
    known = {unknown_src: TrackingSnapshotEntry(src=unknown_src, display_scope="ONLINE_STORE", pattern_name=None)}
    report = inspect_script_tags(live_script_tags=[], known_snapshots=known)
    assert len(report.findings) == 1
    assert "tracking script" in report.findings[0].title
    assert unknown_src not in KNOWN_TRACKING_PATTERNS  # sanity: genuinely unrecognized
