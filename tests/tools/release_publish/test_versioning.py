"""Versioning tests."""

from tools.release_publish.models import Bump
from tools.release_publish.versioning import (
    Version,
    read_version,
    replace_version,
)

from .helpers import check, expect


def test_parse_and_bump() -> None:
    """Versions parse and bump deterministically."""
    version: Version = Version.parse(value="1.2.3")
    expect(
        actual=str(object=version.bump(kind=Bump.MAJOR)),
        expected="2.0.0",
    )
    expect(
        actual=str(object=version.bump(kind=Bump.MINOR)),
        expected="1.3.0",
    )
    expect(
        actual=str(object=version.bump(kind=Bump.PATCH)),
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
