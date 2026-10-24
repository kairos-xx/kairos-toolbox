# Copyright (c) 2026 Project contributors.
"""Tests for the shared JSON value types."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from stationer.partition.json_types import JsonObject, JsonValue
from tests.tools.stationer.helpers import check, expect


def test_json_types_cover_the_union() -> None:
    """Every JSON production fits the JsonValue union."""
    document: JsonObject = {
        "none": None,
        "flag": True,
        "count": 7,
        "ratio": 1.5,
        "name": "x",
        "items": [1, "two"],
        "nested": {"deep": [None]},
    }
    value: JsonValue = cast(typ="JsonValue", val=document)
    check(
        condition=isinstance(value, dict),
        reason="JsonObject is not a dict.",
    )
    expect(actual=document["count"], expected=7)
