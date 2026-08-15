"""Risk assessment — Sprint 3 Assess Risk step.

Pure domain logic, no I/O. Maps a proposed action type to a RiskLevel and
determines whether merchant approval is required given the store's autonomy
configuration.

Risk level taxonomy (SATDD risk commentary, layered on top of — not
contradicting — the Database Architecture doc's `risk_level` enum values):
  - LEVEL_1_SAFE: fully autonomous, deterministic rollback (ALT text, meta
    titles, redirect URLs are canonical examples).
  - LEVEL_2_MODERATE: configurable autonomy — auto-executes only if
    `store.autonomy_level >= 2 AND store.is_autonomous_execution_enabled`;
    otherwise requires approval.
  - LEVEL_3_HIGH / LEVEL_4_CRITICAL: always requires approval / never
    auto-executed. No concrete action type maps to either this sprint — the
    engine still handles them generically for future extensibility.
"""

from __future__ import annotations

from app.domain.enums import ActionType, RiskLevel
from app.domain.models import Store

ACTION_RISK_LEVELS: dict[str, RiskLevel] = {
    ActionType.GENERATE_ALT_TEXT.value: RiskLevel.LEVEL_1_SAFE,
    ActionType.REWRITE_PRODUCT_DESCRIPTION.value: RiskLevel.LEVEL_2_MODERATE,
    # Sprint 4 Step 2: deactivating a discount code is reversible (via
    # discountCodeActivate) but directly changes real, customer-facing
    # pricing/checkout availability — not treated as fully autonomous by
    # default, same tier as a product description rewrite.
    ActionType.DEACTIVATE_DUPLICATE_DISCOUNT.value: RiskLevel.LEVEL_2_MODERATE,
    # Sprint 4 Step 3 (productionization phase update): theme file
    # restoration is always human-in-the-loop — mandatory approval regardless
    # of store autonomy settings, even though Execute may now perform a real,
    # automated `themeFilesUpsert` write for stores whose app installation
    # has Shopify's theme-file-write exemption (see
    # `infrastructure/shopify_client.py::restore_theme_file`). Overwriting an
    # arbitrary theme file is never "genuinely safe to auto-approve" — a full
    # asset overwrite can silently break unrelated storefront behavior in a
    # way NightShift's own diff can't fully characterize — so approval stays
    # mandatory at LEVEL_3_HIGH regardless of write capability; only whether
    # an APPROVED restore executes automatically vs. produces a guided bundle
    # depends on that per-store Shopify grant, and NightShift falls back
    # honestly (see `execute_cognitive_task.py`) when it isn't present.
    ActionType.GENERATE_THEME_RESTORE_GUIDE.value: RiskLevel.LEVEL_3_HIGH,
    # Recreating a removed script tag from our own snapshot is reversible
    # (scriptTagDelete undoes it exactly) but injects a live third-party
    # script into every storefront page render — same moderate tier as
    # deactivating a duplicate discount, for the same "reversible but
    # customer-facing" reasoning.
    ActionType.RECREATE_TRACKING_SCRIPT_TAG.value: RiskLevel.LEVEL_2_MODERATE,
}

RISK_LEVEL_REASONING: dict[RiskLevel, str] = {
    RiskLevel.LEVEL_1_SAFE: (
        "ALT text edits are fully deterministic and reversible (the prior "
        "value is always known and restorable), and touch only metadata, "
        "never pricing, inventory, or checkout-critical fields."
    ),
    RiskLevel.LEVEL_2_MODERATE: (
        "Reversible, but changes customer-facing content or checkout-"
        "relevant availability, so it is not treated as fully autonomous by "
        "default."
    ),
    RiskLevel.LEVEL_3_HIGH: (
        "Requires mandatory human approval regardless of store autonomy "
        "settings."
    ),
    RiskLevel.LEVEL_4_CRITICAL: (
        "Blocked from autonomous execution entirely; manual-only."
    ),
}
"""Generic, level-wide fallback text — used only for an action type with no
entry in `ACTION_TYPE_REASONING` below (an unknown action type, or a future
one not yet given its own sentence). Every currently-wired action type has
its own specific reasoning instead, so a merchant reading one action's audit
log entry never sees another action type's explanation (Sprint 5 Phase 5
bug fix — see CONFLICTS.md item 54: this dict used to be the ONLY source of
reasoning text, keyed by level, so every LEVEL_2_MODERATE action displayed
the same paragraph naming all three LEVEL_2 action types at once)."""

ACTION_TYPE_REASONING: dict[str, str] = {
    ActionType.GENERATE_ALT_TEXT.value: RISK_LEVEL_REASONING[RiskLevel.LEVEL_1_SAFE],
    ActionType.REWRITE_PRODUCT_DESCRIPTION.value: (
        "Product description rewrites are customer-facing content changes "
        "that can affect SEO and conversion; reversible via the stored "
        "prior value, but not treated as fully autonomous by default."
    ),
    ActionType.DEACTIVATE_DUPLICATE_DISCOUNT.value: (
        "Deactivating a duplicate discount code is reversible (re-"
        "activation is always possible) but changes real pricing "
        "availability at checkout, so it is held to the same moderate-risk "
        "bar as other customer-facing content changes."
    ),
    ActionType.RECREATE_TRACKING_SCRIPT_TAG.value: (
        "Recreating a removed tracking script tag from a known-good "
        "snapshot is reversible but customer-facing (it injects a live "
        "third-party script into every storefront page render), so it is "
        "held to the same moderate-risk bar as other customer-facing "
        "content changes."
    ),
    ActionType.GENERATE_THEME_RESTORE_GUIDE.value: (
        "Requires mandatory human approval regardless of store autonomy "
        "settings — overwriting a theme file is never treated as safe to "
        "auto-approve. Once approved, NightShift attempts a real, automated "
        "restore; most Shopify app installations (this one included, by "
        "default) lack the manual Shopify exemption required to write theme "
        "files, so approval most often still produces a guided fix for the "
        "merchant to apply themselves — NightShift is honest about which "
        "outcome occurred in the Work Log either way."
    ),
}
"""One sentence per action type — never a level-wide blob that names other,
unrelated action types. Keys mirror `ACTION_RISK_LEVELS` exactly; every
action type mapped there also has an entry here."""


def assess_risk_level(action_type: str) -> tuple[RiskLevel, str]:
    """Returns (risk_level, human-readable reasoning) for a given action
    type. Unknown action types conservatively default to LEVEL_3_HIGH
    (always requires approval) rather than silently allowing autonomy — and,
    having no entry in `ACTION_TYPE_REASONING`, fall back to that level's
    generic text rather than raising."""
    level = ACTION_RISK_LEVELS.get(action_type, RiskLevel.LEVEL_3_HIGH)
    reasoning = ACTION_TYPE_REASONING.get(action_type, RISK_LEVEL_REASONING[level])
    return level, reasoning


def requires_approval(risk_level: RiskLevel, store: Store) -> bool:
    """Store-autonomy-based policy. Does NOT account for merchant-memory
    overrides — see domain/merchant_memory.py for that separate, additive
    check; callers must OR the two together."""
    if risk_level == RiskLevel.LEVEL_1_SAFE:
        return not store.is_autonomous_execution_enabled
    if risk_level == RiskLevel.LEVEL_2_MODERATE:
        return not (store.is_autonomous_execution_enabled and store.autonomy_level >= 2)
    # LEVEL_3_HIGH / LEVEL_4_CRITICAL
    return True
