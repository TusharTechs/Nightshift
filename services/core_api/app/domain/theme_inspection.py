"""Theme Inspection Engine — Sprint 4 Step 3, Theme Guardian's Observe step.

Pure domain logic, no framework or Shopify-client imports (mirrors
`discount_inspection.py`'s own contract). Compares the current live content
of a small, fixed watch-list of critical theme files against each file's
last known-good baseline snapshot, and reports any divergence.

Watch-list, not whole-theme scanning: a live Shopify theme can contain
hundreds of files, and diffing/LLM-explaining all of them every shift would
be neither cheap nor useful — mirrors the same bounded-scan precedent as
`MAX_INSPECTION_SKUS`/`MAX_DISCOUNTS_SCANNED` (Sprint 2/Step 2). The default
watch-list below covers the file the Demo Incident Generator's Scenario 2
("Rogue Developer Theme Break") actually corrupts — Dawn/OS-2.0-family
themes render the product page's Buy Button block from
`sections/main-product.liquid`. A real deployment would make this
merchant-configurable; hardcoded here as an explicit, documented MVP scope
boundary.

First-observation seeding: if no baseline snapshot exists yet for a given
(theme_id, filename), that first read becomes the baseline — there is
nothing to diff against yet, so no finding is raised, exactly mirroring how
`discount_inspection.py`'s Checkout Specialist can't flag "duplicate"
before at least two live discounts have been seen. Baseline snapshots are
never automatically overwritten again after that (see the migration's own
docstring for why) — this module only ever tells the caller whether a
supplied baseline still matches supplied current content; the caller
(`services/workers/tasks/theme_inspection.py`) owns snapshot persistence.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

DEFAULT_WATCHED_FILENAMES: tuple[str, ...] = ("sections/main-product.liquid",)


def compute_checksum(content: str) -> str:
    return hashlib.md5(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ThemeFileBaseline:
    filename: str
    content: str
    checksum_md5: str


@dataclass(frozen=True)
class ThemeFileDiffFinding:
    filename: str
    theme_id: str
    baseline_content: str
    current_content: str
    baseline_checksum: str
    current_checksum: str
    changed_line_count: int
    affected_resources: list[str]
    evidence: dict


@dataclass(frozen=True)
class ThemeInspectionReport:
    files_scanned: int
    findings: list[ThemeFileDiffFinding]
    newly_baselined_filenames: list[str]


def _line_diff_count(baseline_content: str, current_content: str) -> int:
    """Coarse, dependency-free line-level diff count — good enough to size
    the LLM explanation prompt and to report "N lines changed" without
    pulling in `difflib`'s full opcodes for what's ultimately just a
    severity/summary signal, not a patch-generation algorithm (the actual
    patch handed to the merchant is the full baseline content, not a
    line-diff — see `domain/agents/theme_guardian.py`)."""
    baseline_lines = baseline_content.splitlines()
    current_lines = current_content.splitlines()
    max_len = max(len(baseline_lines), len(current_lines))
    changed = 0
    for i in range(max_len):
        b = baseline_lines[i] if i < len(baseline_lines) else None
        c = current_lines[i] if i < len(current_lines) else None
        if b != c:
            changed += 1
    return changed


def inspect_theme_files(
    *,
    theme_id: str,
    current_files: dict[str, str],
    baselines: dict[str, ThemeFileBaseline],
) -> ThemeInspectionReport:
    """`current_files` maps filename -> live content (as freshly fetched via
    `ShopifyGraphQLClient.fetch_theme_files`). `baselines` maps filename ->
    the last known-good `ThemeFileBaseline` (empty/missing entries mean
    "never observed before" for that file).

    Returns findings for every watched file whose current checksum diverges
    from its baseline, plus the list of filenames seen for the first time
    this call (the caller persists these as new baseline rows; this module
    never persists anything itself).
    """
    findings: list[ThemeFileDiffFinding] = []
    newly_baselined: list[str] = []

    for filename, current_content in current_files.items():
        current_checksum = compute_checksum(current_content)
        baseline = baselines.get(filename)

        if baseline is None:
            newly_baselined.append(filename)
            continue

        if baseline.checksum_md5 == current_checksum:
            continue

        changed_lines = _line_diff_count(baseline.content, current_content)
        findings.append(
            ThemeFileDiffFinding(
                filename=filename,
                theme_id=theme_id,
                baseline_content=baseline.content,
                current_content=current_content,
                baseline_checksum=baseline.checksum_md5,
                current_checksum=current_checksum,
                changed_line_count=changed_lines,
                affected_resources=[theme_id, filename],
                evidence={
                    "check": "theme_file_diverged_from_baseline",
                    "filename": filename,
                    "theme_id": theme_id,
                    "changed_line_count": changed_lines,
                    # Sprint 4: content-aware — includes current_checksum,
                    # not just (theme_id, filename). Lets the worker task
                    # recognize "this EXACT divergence already has an open
                    # issue/approval" across shifts (so an unfixed, unchanged
                    # problem reuses its existing pending approval instead of
                    # spawning a duplicate every recurring-scheduler cycle),
                    # while still surfacing a genuinely NEW issue if the file
                    # changes again in a different way while the old one is
                    # still open — a bug found live: without the checksum, a
                    # second real edit was being silently swallowed into the
                    # stale first issue instead of ever being flagged.
                    "dedup_key": f"theme:{theme_id}:{filename}:{current_checksum}",
                },
            )
        )

    return ThemeInspectionReport(
        files_scanned=len(current_files), findings=findings, newly_baselined_filenames=newly_baselined
    )
