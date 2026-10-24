"""Configuration tests."""

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import pytest

from tools.stationer.config import Config, load_config

from .helpers import expect

if TYPE_CHECKING:
    from tools.stationer import Config


def test_load_config() -> None:
    """A monorepo configuration loads with dependencies."""
    with TemporaryDirectory() as directory:
        path: Path = Path(directory) / "release.toml"
        path.write_text(
            data='[repository]\nowner="o"\nname="r"\n[projects.a]\npath="a"\nversion="1.0.0"\n[projects.b]\npath="b"\nversion="2.0.0"\ndepends_on=["a"]\n',
            encoding="utf-8",
        )
        config: Config = load_config(path=path)
        expect(
            actual=tuple(project.name for project in config.projects),
            expected=("a", "b"),
        )


def test_version_is_read_from_the_first_version_file() -> None:
    """Omitting ``version`` keeps the version file the single source."""
    with TemporaryDirectory() as directory:
        root: Path = Path(directory)
        (root / "p").mkdir()
        (root / "p" / "product.py").write_text(
            data='class P:\n    version: Final[str] = "3.4.5"\n',
            encoding="utf-8",
        )
        path: Path = root / "release.toml"
        path.write_text(
            data=(
                '[repository]\nowner="o"\nname="r"\n'
                '[projects.a]\npath="p"\n'
                'version_files=["p/product.py"]\n'
            ),
            encoding="utf-8",
        )
        expect(
            actual=load_config(path=path).project(name="a").version,
            expected="3.4.5",
        )


def test_missing_version_without_a_readable_file_is_an_error() -> None:
    """A project needs either ``version`` or a file that contains one."""
    with TemporaryDirectory() as directory:
        path: Path = Path(directory) / "release.toml"
        path.write_text(
            data='[repository]\nowner="o"\nname="r"\n[projects.a]\npath="p"\n',
            encoding="utf-8",
        )
        with pytest.raises(expected_exception=ValueError, match="version"):
            load_config(path=path)
