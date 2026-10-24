# Copyright (c) 2026 Project contributors.
"""Shared JSON value types for the partition engine and callers."""

from __future__ import annotations

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)
type JsonObject = dict[str, JsonValue]
