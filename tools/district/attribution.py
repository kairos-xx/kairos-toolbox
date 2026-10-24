# Copyright (c) 2026 Project contributors.
"""Resolve agent attribution from explicit evidence without guessing."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Final


class AttributionStrength(IntEnum):
    """Rank attribution evidence from weakest to strongest.

    Attributes:
        unknown: No usable attribution evidence.
        heuristic: Provider guessed from weak heuristics.
        process_tree: Provider inferred from process ancestry.
        editor: Provider inferred from editor identity.
        environment: Provider declared by environment variables.
        provider_hook: Provider declared by a provider hook.
        execution_token: Provider proven by an execution token.

    """

    unknown = 0
    heuristic = 10
    process_tree = 20
    editor = 30
    environment = 40
    provider_hook = 50
    execution_token = 60


@dataclass(frozen=True, slots=True, kw_only=True)
class AttributionEvidence:
    """Describe one provider-attribution observation.

    Attributes:
        provider: Stable provider identifier, or ``unknown``.
        provider_session: Provider conversation/session identifier.
        source: Evidence source.
        detail: Human-readable evidence detail.
        surface: Invocation surface such as CLI, desktop, or extension.
        editor: Editor identity when applicable.
        workspace: Workspace associated with the observation.

    """

    provider: str
    provider_session: str = ""
    source: AttributionStrength = AttributionStrength.unknown
    detail: str = ""
    surface: str = ""
    editor: str = ""
    workspace: str = ""

    @property
    def authoritative(self) -> bool:
        """Return whether the evidence is explicit rather than inferred.

        Returns:
            Whether the evidence is authoritative.

        """
        return self.source >= AttributionStrength.environment


@dataclass(frozen=True, slots=True, kw_only=True)
class AttributionDecision:
    """Describe the selected attribution and all supporting evidence.

    Attributes:
        selected: Selected evidence, or an explicit unknown observation.
        evidence: Evidence ordered strongest first.
        conflicted: Whether equally strong evidence disagreed on provider.

    """

    selected: AttributionEvidence
    evidence: tuple[AttributionEvidence, ...]
    conflicted: bool


class AttributionResolver:
    """Resolve provider identity using deterministic evidence precedence.

    Attributes:
        unknown_provider: Provider label used when evidence is absent.

    """

    unknown_provider: Final[str] = "unknown"

    @classmethod
    def resolve(
        cls,
        *,
        evidence: tuple[AttributionEvidence, ...],
    ) -> AttributionDecision:
        """Select the strongest non-conflicting provider evidence.

        Args:
            evidence: Available attribution observations.

        Returns:
            Deterministic attribution decision. Conflicting observations at
            the strongest level intentionally resolve to ``unknown``.

        """
        ordered: tuple[AttributionEvidence, ...] = tuple(
            sorted(
                evidence,
                key=cls._sort_key,
                reverse=True,
            ),
        )
        candidates: tuple[AttributionEvidence, ...] = tuple(
            item
            for item in ordered
            if item.provider and item.provider != cls.unknown_provider
        )
        if not candidates:
            return AttributionDecision(
                selected=AttributionEvidence(provider=cls.unknown_provider),
                evidence=ordered,
                conflicted=False,
            )
        strongest: AttributionStrength = candidates[0].source
        peers: tuple[AttributionEvidence, ...] = tuple(
            item for item in candidates if item.source == strongest
        )
        providers: frozenset[str] = frozenset(item.provider for item in peers)
        if len(providers) != 1:
            return AttributionDecision(
                selected=AttributionEvidence(
                    provider=cls.unknown_provider,
                    source=strongest,
                    detail="conflicting equally strong attribution evidence",
                ),
                evidence=ordered,
                conflicted=True,
            )
        return AttributionDecision(
            selected=peers[0],
            evidence=ordered,
            conflicted=False,
        )

    @staticmethod
    def _sort_key(item: AttributionEvidence, /) -> int:
        """Return the numeric strength used for evidence ordering.

        Args:
            item: Attribution evidence.

        Returns:
            Numeric evidence strength.

        """
        return int(item.source)
