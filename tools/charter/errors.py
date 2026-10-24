# Copyright (c) 2026 Project contributors.
"""Typed errors shared by every charter backend."""

from __future__ import annotations


class CharterError(RuntimeError):
    """Report charter setup, request and lifecycle failures."""
