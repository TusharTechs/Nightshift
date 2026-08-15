"""Agent abstract base class — Sprint 2 Backend Specification directory
layout (`domain/agents/base.py`).

Every specialist AI Employee (Product Quality this sprint; Checkout,
Tracking, Discount, Performance in later sprints per the Hackathon MVP
Spec's 5-employee roster) wraps a single LLM reasoning cycle: build a
grounded prompt from real store data, request structured JSON output,
validate it, and fall back to a deterministic rule-based result if the
model call or schema validation fails.

Sprint 3 (AI Trust & Execution) adds `propose_action` — the Plan step that
turns a persisted `Issue` into a concrete, executable `ProposedAction` for
the small set of issue types this sprint made auto-fixable.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

from app.domain.models import Issue

# No circular import here: app.domain.models has zero knowledge of
# app.domain.agents (it only imports app.domain.enums), so a normal import
# is safe and simpler than a TYPE_CHECKING guard.


class AgentAnalysisResult(BaseModel):
    """Base contract every specialist agent's structured output must
    satisfy. Concrete agents extend this with their own issue schema."""

    executive_summary: str


class ProposedAction(BaseModel):
    """Result of an agent's Plan step (Observe/Reason already happened by
    the time an Issue exists; this is the 'what should I do about it'
    step) — Sprint 3 AI Trust & Execution lifecycle."""

    action_type: str
    execution_plan: dict
    """Shopify-mutation-ready plan: mutation name, target GIDs, new value(s),
    before_state (for verification/rollback), and a `rollback` sub-dict with
    the compensating mutation's parameters."""


class Agent(ABC):
    """Abstract base class for specialist AI Employees."""

    identifier: str
    domain_category: str

    @abstractmethod
    async def analyze(self, inspection_data: dict[str, Any]) -> AgentAnalysisResult:
        """Reason over deterministic inspection findings and return
        structured analysis (issue descriptions, revenue impact, confidence
        scores). Must never raise on a model/schema failure — implementers
        are responsible for catching `GeminiSchemaValidationError` (or
        equivalent) internally and returning a deterministic fallback
        result instead, per the "never proceed with malformed AI output"
        rule."""
        raise NotImplementedError

    def propose_action(self, issue: Issue) -> ProposedAction | None:
        """Plan step (Sprint 3): given a persisted Issue, propose a
        concrete, executable fix. Returns None if this agent has no
        automated fix for this issue (most issues this sprint still have no
        fix path — that's expected and fine; they just stay OPEN,
        unresolved, same as today). Default implementation returns None;
        concrete agents override this for the specific fix types they
        support."""
        return None
