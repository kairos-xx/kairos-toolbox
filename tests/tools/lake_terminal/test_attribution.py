# Copyright (c) 2026 Project contributors.
"""Tests for deterministic attribution evidence resolution."""

from tools.lake_terminal.lake_terminal.attribution import (
    AttributionDecision,
    AttributionEvidence,
    AttributionResolver,
    AttributionStrength,
)

from .helpers import check, expect


def test_stronger_evidence_wins() -> None:
    """Prefer explicit provider evidence over process-tree inference."""
    decision: AttributionDecision = AttributionResolver.resolve(
        evidence=(
            AttributionEvidence(
                provider="cursor",
                source=AttributionStrength.process_tree,
            ),
            AttributionEvidence(
                provider="codex",
                provider_session="session-a",
                source=AttributionStrength.provider_hook,
            ),
        ),
    )
    expect(actual=decision.selected.provider, expected="codex")
    expect(
        actual=decision.selected.provider_session,
        expected="session-a",
    )
    check(
        condition=not decision.conflicted,
        reason="stronger evidence must not conflict",
    )


def test_equal_strength_conflict_becomes_unknown() -> None:
    """Refuse to guess when equally strong provider evidence conflicts."""
    decision: AttributionDecision = AttributionResolver.resolve(
        evidence=(
            AttributionEvidence(
                provider="claude",
                source=AttributionStrength.environment,
            ),
            AttributionEvidence(
                provider="codex",
                source=AttributionStrength.environment,
            ),
        ),
    )
    expect(actual=decision.selected.provider, expected="unknown")
    check(
        condition=decision.conflicted,
        reason="equal-strength conflict must be flagged",
    )
