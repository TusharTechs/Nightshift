"""Unit tests for the Theme Inspection Engine (Sprint 4 Step 3 — Theme
Guardian's Observe step). Pure domain logic, no I/O.
"""

from __future__ import annotations

from app.domain.theme_inspection import (
    ThemeFileBaseline,
    compute_checksum,
    inspect_theme_files,
)

THEME_ID = "gid://shopify/OnlineStoreTheme/1"
FILENAME = "sections/main-product.liquid"


def test_first_observation_seeds_baseline_and_raises_no_finding():
    current_files = {FILENAME: "{% render 'buy-buttons' %}"}
    report = inspect_theme_files(theme_id=THEME_ID, current_files=current_files, baselines={})

    assert report.findings == []
    assert report.newly_baselined_filenames == [FILENAME]


def test_matching_checksum_against_baseline_raises_no_finding():
    content = "{% render 'buy-buttons' %}"
    baseline = ThemeFileBaseline(filename=FILENAME, content=content, checksum_md5=compute_checksum(content))

    report = inspect_theme_files(
        theme_id=THEME_ID, current_files={FILENAME: content}, baselines={FILENAME: baseline}
    )

    assert report.findings == []
    assert report.newly_baselined_filenames == []


def test_divergent_content_produces_a_finding_with_both_versions_and_line_diff_count():
    baseline_content = "line1\n{% render 'buy-buttons' %}\nline3"
    current_content = "line1\nline3"
    baseline = ThemeFileBaseline(
        filename=FILENAME, content=baseline_content, checksum_md5=compute_checksum(baseline_content)
    )

    report = inspect_theme_files(
        theme_id=THEME_ID, current_files={FILENAME: current_content}, baselines={FILENAME: baseline}
    )

    assert len(report.findings) == 1
    finding = report.findings[0]
    assert finding.filename == FILENAME
    assert finding.theme_id == THEME_ID
    assert finding.baseline_content == baseline_content
    assert finding.current_content == current_content
    assert finding.changed_line_count >= 1
    assert finding.affected_resources == [THEME_ID, FILENAME]
    assert finding.evidence["check"] == "theme_file_diverged_from_baseline"
    # Sprint 4: content-aware — lets the worker task reuse an existing open
    # issue for this exact (file, content) pair across shifts instead of
    # creating a duplicate, while still surfacing a genuinely new issue if
    # the file changes again in a different way while the old one is open.
    assert finding.evidence["dedup_key"] == f"theme:{THEME_ID}:{FILENAME}:{finding.current_checksum}"


def test_a_second_different_edit_produces_a_different_dedup_key_than_the_first():
    """Live-found bug: a file already flagged as diverged (issue still open,
    unresolved) that then changes AGAIN in a different way must produce a
    distinct dedup_key — otherwise the worker task's dedup check silently
    swallows the second, genuinely different edit into the stale first
    issue, and the merchant never sees the new problem at all."""
    baseline_content = "line1\n{% render 'buy-buttons' %}\nline3"
    baseline = ThemeFileBaseline(
        filename=FILENAME, content=baseline_content, checksum_md5=compute_checksum(baseline_content)
    )

    first_edit = "line1\nline3"
    second_edit = "line1\n{% comment %} removed differently {% endcomment %}"

    first_report = inspect_theme_files(
        theme_id=THEME_ID, current_files={FILENAME: first_edit}, baselines={FILENAME: baseline}
    )
    second_report = inspect_theme_files(
        theme_id=THEME_ID, current_files={FILENAME: second_edit}, baselines={FILENAME: baseline}
    )

    first_key = first_report.findings[0].evidence["dedup_key"]
    second_key = second_report.findings[0].evidence["dedup_key"]
    assert first_key != second_key


def test_baseline_is_never_mutated_by_this_module_caller_owns_persistence():
    # inspect_theme_files is pure — it never writes anything; the caller
    # (services/workers/tasks/theme_inspection.py) owns snapshot persistence.
    content = "unchanged"
    baseline = ThemeFileBaseline(filename=FILENAME, content=content, checksum_md5=compute_checksum(content))
    report = inspect_theme_files(
        theme_id=THEME_ID, current_files={FILENAME: content}, baselines={FILENAME: baseline}
    )
    assert baseline.content == content  # untouched
    assert report.files_scanned == 1
