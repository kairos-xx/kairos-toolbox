"""Versioning tests."""

from stationer.versioning import is_newer

from tools.stationer.models import Bump
from tools.stationer.versioning import (
    Version,
    read_version,
    replace_version,
)

from .helpers import check, expect


def test_parse_and_bump() -> None:
    """Versions parse and bump deterministically."""
    version: Version = Version.parse(value="1.2.3")
    expect(
        actual=str(object=version.bump(kind=Bump.major)),
        expected="2.0.0",
    )
    expect(
        actual=str(object=version.bump(kind=Bump.minor)),
        expected="1.3.0",
    )
    expect(
        actual=str(object=version.bump(kind=Bump.patch)),
        expected="1.2.4",
    )


def test_read_and_replace_version_in_toml_and_python_forms() -> None:
    """Version lines are found in TOML and annotated Python assignments."""
    toml: str = '[project]\nname = "x"\nversion = "1.0.0"\n'
    python: str = 'class Product:\n    version: Final[str] = "1.0.0"\n'
    expect(actual=read_version(text=toml), expected="1.0.0")
    expect(actual=read_version(text=python), expected="1.0.0")
    check(
        condition=read_version(text="nothing here\n") is None,
        reason="no version assignment must read as None",
    )
    updated, count = replace_version(text=python, old="1.0.0", new="1.0.1")
    expect(actual=count, expected=1)
    check(
        condition='version: Final[str] = "1.0.1"' in updated,
        reason="annotated assignment must be rewritten",
    )
    untouched, missed = replace_version(
        text=toml,
        old="9.9.9",
        new="1.0.1",
    )
    expect(actual=missed, expected=0)
    expect(actual=untouched, expected=toml)


def test_replace_version_ignores_lookalike_names() -> None:
    """Only a bare ``version`` assignment is rewritten."""
    text: str = 'python_version = "1.0.0"\nversion = "1.0.0"\n'
    updated, count = replace_version(text=text, old="1.0.0", new="2.0.0")
    expect(actual=count, expected=1)
    expect(
        actual=updated,
        expected='python_version = "1.0.0"\nversion = "2.0.0"\n',
    )


def test_is_newer_orders_stable_over_prerelease() -> None:
    """``1.2.0`` outranks ``1.2.0rc1``; prereleases opt in."""
    check(
        condition=is_newer(candidate="1.2.0", current="1.2.0rc1"),
        reason="Stable did not outrank its prerelease.",
    )
    check(
        condition=not is_newer(candidate="1.2.0rc1", current="1.2.0"),
        reason="Prerelease outranked stable without opt-in.",
    )
    check(
        condition=not is_newer(candidate="1.3.0rc1", current="1.2.0"),
        reason="Prerelease counted on the stable channel.",
    )
    check(
        condition=is_newer(
            candidate="1.3.0rc1",
            current="1.2.0",
            allow_prerelease=True,
        ),
        reason="allow_prerelease ignored.",
    )


def test_is_newer_rejects_malformed_versions() -> None:
    """Malformed input returns ``False`` rather than raising."""
    for candidate in ("", "1.2", "x.y.z", "1.2.3.4", "v1"):
        check(
            condition=not is_newer(candidate=candidate, current="1.0.0"),
            reason=f"{candidate!r} parsed as a version.",
        )
    check(
        condition=is_newer(candidate="2.0.0", current="1.9.9"),
        reason="Major bump not newer.",
    )
    check(
        condition=not is_newer(candidate="1.0.0", current="1.0.0"),
        reason="Equal version counted as newer.",
    )
