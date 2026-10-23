# Copyright (c) 2026 Project contributors.
"""Redact sensitive values and load retention/privacy policy."""

from __future__ import annotations

from dataclasses import dataclass
from json import JSONDecodeError, loads
from re import IGNORECASE, Pattern
from re import compile as compile_pattern
from re import error as regex_error
from typing import TYPE_CHECKING, Final, cast

from .product import state_directory

if TYPE_CHECKING:
    from pathlib import Path
type PrivacyValue = bool | int | list[str]


@dataclass(frozen=True, slots=True, kw_only=True)
class PrivacyPolicy:
    """Configure retention, output persistence, and custom redaction.

    Attributes:
        history_days: History days.
        event_days: Event days.
        persist_output: Persist output.
        output_limit_bytes: Output limit bytes.
        redact_patterns: Redact patterns.

    """

    history_days: int = 30
    event_days: int = 90
    persist_output: bool = False
    output_limit_bytes: int = 262_144
    redact_patterns: tuple[str, ...] = ()

    @classmethod
    def load(cls, *, home: Path) -> PrivacyPolicy:
        """Load ``~/.lake_terminal/privacy.json`` when present.

        Args:
            home: Home.

        Returns:
            The resulting value.

        """
        path: Path = state_directory(home=home) / "privacy.json"
        if not path.is_file():
            return PrivacyPolicy()
        try:
            raw: object = loads(s=path.read_text(encoding="utf-8"))
        except JSONDecodeError:
            return PrivacyPolicy()
        if not isinstance(raw, dict):
            return PrivacyPolicy()
        value: dict[str, PrivacyValue] = cast(
            typ="dict[str, PrivacyValue]",
            val=raw,
        )
        history_days: int = cls._integer(
            value=value.get("history_days"),
            default=30,
            minimum=0,
        )
        event_days: int = cls._integer(
            value=value.get("event_days"),
            default=90,
            minimum=0,
        )
        persist_output: bool = cls._boolean(
            value=value.get("persist_output"),
            default=False,
        )
        output_limit_bytes: int = cls._integer(
            value=value.get("output_limit_bytes"),
            default=262_144,
            minimum=4096,
        )
        redact_patterns: tuple[str, ...] = cls._patterns(
            value=value.get("redact_patterns"),
        )
        return PrivacyPolicy(
            history_days=history_days,
            event_days=event_days,
            persist_output=persist_output,
            output_limit_bytes=output_limit_bytes,
            redact_patterns=redact_patterns,
        )

    @staticmethod
    def _integer(
        *,
        value: PrivacyValue | None,
        default: int,
        minimum: int,
    ) -> int:
        """Normalize an integer privacy setting.

        Args:
            value: Raw setting value.
            default: Default value.
            minimum: Minimum accepted value.

        Returns:
            Normalized integer value.

        """
        if not isinstance(value, int) or isinstance(value, bool):
            return default
        return max(minimum, value)

    @staticmethod
    def _boolean(
        *,
        value: PrivacyValue | None,
        default: bool,
    ) -> bool:
        """Normalize a boolean privacy setting.

        Args:
            value: Raw setting value.
            default: Default value.

        Returns:
            Normalized boolean value.

        """
        if not isinstance(value, bool):
            return default
        return value

    @staticmethod
    def _patterns(
        *,
        value: PrivacyValue | None,
    ) -> tuple[str, ...]:
        """Normalize custom redaction patterns.

        Args:
            value: Raw setting value.

        Returns:
            Custom redaction patterns.

        """
        if not isinstance(value, list):
            return ()
        return tuple(value)


class Redactor:
    """Replace common and user-configured credential forms.

    Attributes:
        replacement: Replacement.
        patterns: Patterns.

    """

    replacement: Final[str] = "[REDACTED]"
    patterns: Final[tuple[Pattern[str], ...]] = (
        compile_pattern(
            pattern=(
                r"(?i)(api[_-]?key|token|password|passwd|secret)"
                r"\s*[:=]\s*([^\s'\"]+)"
            ),
        ),
        compile_pattern(
            pattern=(
                r"(?i)(authorization:\s*(?:bearer|basic)\s+)"
                r"([^\s]+)"
            ),
        ),
        compile_pattern(
            pattern=r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_\-]{12,}\b",
            flags=IGNORECASE,
        ),
    )

    @classmethod
    def redact(
        cls,
        *,
        value: str,
        extra_patterns: tuple[str, ...] = (),
    ) -> str:
        """Return text with known secret patterns removed.

        Args:
            value: Value.
            extra_patterns: Extra patterns.

        Returns:
            The resulting value.

        """
        result: str = value
        for pattern in cls.patterns:
            if pattern.groups > 1:
                result = pattern.sub(
                    repl=rf"\1{cls.replacement}",
                    string=result,
                )
            else:
                result = pattern.sub(
                    repl=cls.replacement,
                    string=result,
                )
        for expression in extra_patterns:
            try:
                result = compile_pattern(
                    pattern=expression,
                ).sub(
                    repl=cls.replacement,
                    string=result,
                )
            except regex_error:
                continue
        return result
