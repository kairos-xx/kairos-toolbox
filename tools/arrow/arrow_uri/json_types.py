# Copyright (c) 2026 Project contributors.
"""Strict JSON-compatible value types used across Arrow."""

from __future__ import annotations

type JSONScalar = None | bool | int | float | str
type JSONValue = JSONScalar | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]
