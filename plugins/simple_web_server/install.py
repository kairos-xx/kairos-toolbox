#!/usr/bin/env python3
"""Install or uninstall the extension in VS Code-compatible editors."""

from __future__ import annotations

from argparse import ArgumentParser, Namespace
from dataclasses import dataclass
from json import loads
from pathlib import Path
from shutil import copytree, rmtree, which
from sys import stdout
from typing import Final, cast


@dataclass(frozen=True, slots=True)
class Editor:
    """Describe one installable VS Code-compatible editor.

    Attributes:
        identifier: Stable CLI/editor identifier.
        label: Human-readable product name.
        command: Discovered command-line executable, when available.
        extensions: User extension directory, when known.

    """

    identifier: str
    label: str
    command: str | None
    extensions: Path | None


class Constants:
    """Group immutable installer metadata.

    Attributes:
        reset: ANSI reset sequence.
        bold: ANSI bold sequence.
        dim: ANSI dim sequence.
        cyan: ANSI cyan sequence.
        green: ANSI green sequence.
        yellow: ANSI yellow sequence.
        red: ANSI red sequence.
        products: Supported editors as (id, label, CLIs, extension path).

    """

    reset: Final[str] = "\033[0m"
    bold: Final[str] = "\033[1m"
    dim: Final[str] = "\033[2m"
    cyan: Final[str] = "\033[36m"
    green: Final[str] = "\033[32m"
    yellow: Final[str] = "\033[33m"
    red: Final[str] = "\033[31m"
    products: Final[tuple[tuple[str, str, tuple[str, ...], str], ...]] = (
        ("code", "Visual Studio Code", ("code",), ".vscode/extensions"),
        (
            "code-insiders",
            "Visual Studio Code Insiders",
            ("code-insiders",),
            ".vscode-insiders/extensions",
        ),
        (
            "vscodium",
            "VSCodium",
            ("codium", "vscodium"),
            ".vscode-oss/extensions",
        ),
        ("cursor", "Cursor", ("cursor",), ".cursor/extensions"),
        ("windsurf", "Windsurf", ("windsurf",), ".windsurf/extensions"),
        ("devin", "Devin", ("devin",), ".devin/extensions"),
    )


class Installer:
    """Discover editors and manage extension installation."""

    def __init__(self, *, project: Path) -> None:
        """Initialize the installer.

        Args:
            project: Extension project root.

        """
        self.project: Path = project.resolve()
        self.manifest: dict[str, str] = self._manifest()

    @classmethod
    def select(cls, *, editors: tuple[Editor, ...]) -> tuple[Editor, ...]:
        """Prompt for one or more editors.

        Args:
            editors: Available editors.

        Returns:
            Selected editors.

        Raises:
            ValueError: If the entered selection is invalid.

        """
        cls.report(
            message="Detected VS Code-compatible editors:",
            color=Constants.cyan,
            bold=True,
        )
        for index, editor in enumerate(iterable=editors, start=1):
            detail: str = editor.command or (
                str(object=editor.extensions)
                if editor.extensions is not None
                else "no extension directory"
            )
            _ = stdout.write(
                f"  {Constants.bold}{index}.{Constants.reset} "
                f"{editor.label} {Constants.dim}{detail}{Constants.reset}\n",
            )
        _ = stdout.write(
            f"  {Constants.bold}a.{Constants.reset} All detected editors\n\n"
            "Select destination(s), e.g. 1,3 or a: ",
        )
        answer: str = input().strip().lower()
        if answer == "a":
            return editors
        indices: tuple[int, ...] = tuple(
            int(value.strip())
            for value in answer.split(sep=",")
            if value.strip()
        )
        invalid: bool = not indices or any(
            index < 1 or index > len(editors) for index in indices
        )
        if invalid:
            msg: str = "Invalid editor selection"
            raise ValueError(msg)
        return tuple(editors[index - 1] for index in indices)

    @staticmethod
    def discover() -> tuple[Editor, ...]:
        """Discover supported editor installations.

        Returns:
            Detected editors in stable product order.

        """
        home: Path = Path.home()
        found: list[Editor] = []
        for identifier, label, commands, relative in Constants.products:
            command: str | None = next(
                (
                    candidate
                    for name in commands
                    if (candidate := which(cmd=name)) is not None
                ),
                None,
            )
            extensions: Path = home / relative
            if command is not None or extensions.parent.exists():
                found.append(
                    Editor(
                        identifier=identifier,
                        label=label,
                        command=command,
                        extensions=extensions,
                    ),
                )
        return tuple(found)

    @staticmethod
    def report(
        *,
        message: str,
        color: str,
        bold: bool = False,
        symbol: str = "",
    ) -> None:
        """Write one ANSI-formatted status line.

        Args:
            message: Text to write.
            color: ANSI color sequence.
            bold: Whether to use bold output.
            symbol: Leading status glyph.

        """
        marker: str = f"{symbol} " if symbol else ""
        prefix: str = f"{Constants.bold if bold else ''}{color}"
        _ = stdout.write(f"{prefix}{marker}{message}{Constants.reset}\n")

    def banner(self) -> None:
        """Write the installer banner line."""
        label: str = f"{self.manifest['name']} {self.manifest['version']}"
        self.report(
            message=f"==> Extension installer: {label}",
            color=Constants.cyan,
            bold=True,
        )

    def install(self, *, editor: Editor) -> None:
        """Install into one editor.

        Args:
            editor: Destination editor.

        Raises:
            RuntimeError: If the editor has no known extension directory.

        """
        if editor.extensions is None:
            msg: str = f"No extension directory known for {editor.label}"
            raise RuntimeError(msg)
        target: Path = (
            editor.extensions
            / f"local.{self.manifest['name']}-{self.manifest['version']}"
        )
        if target.exists():
            rmtree(path=target)
        editor.extensions.mkdir(parents=True, exist_ok=True)
        _ = copytree(src=self.project / "src", dst=target)
        self.report(
            message=f"Installed in {editor.label}",
            color=Constants.green,
            symbol="✓",
        )
        _ = stdout.write(f"{Constants.dim}    {target}{Constants.reset}\n")

    def uninstall(self, *, editor: Editor) -> None:
        """Uninstall from one editor.

        Args:
            editor: Destination editor.

        """
        if editor.extensions is None or not editor.extensions.exists():
            return
        prefix: str = f"local.{self.manifest['name']}-"
        removed: int = 0
        for target in editor.extensions.iterdir():
            if target.is_dir() and target.name.startswith(prefix):
                rmtree(path=target)
                removed += 1
        self.report(
            message=f"Removed {removed} installation(s) from {editor.label}",
            color=Constants.yellow,
            symbol="-",
        )

    def _manifest(self) -> dict[str, str]:
        """Read the minimal manifest fields required by the installer.

        Returns:
            Extension name and version.

        """
        raw: dict[str, str] = cast(
            typ="dict[str, str]",
            val=loads(
                s=(self.project / "src" / "package.json").read_text(
                    encoding="utf-8",
                ),
            ),
        )
        return {"name": raw["name"], "version": raw["version"]}


def _arguments(*, arguments: tuple[str, ...] | None = None) -> Namespace:
    """Parse command-line arguments.

    Args:
        arguments: Explicit argument tuple, when dispatching.

    Returns:
        Parsed arguments.

    """
    parser: ArgumentParser = ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove the extension instead of installing it.",
    )
    _ = parser.add_argument(
        "--list",
        action="store_true",
        help="List detected compatible editors and exit.",
    )
    _ = parser.add_argument(
        "--editor",
        action="append",
        default=[],
        help=(
            "Select an editor id non-interactively;"
            " repeat for multiple editors."
        ),
    )
    return parser.parse_args(
        args=None if arguments is None else list(arguments),
    )


def main(*, arguments: tuple[str, ...] | None = None) -> int:
    """Run the installer.

    Args:
        arguments: Explicit argument tuple, when dispatching.

    Returns:
        Process exit status.

    Raises:
        ValueError: If a requested editor id was not detected.

    """
    parsed: Namespace = (
        _arguments() if arguments is None else _arguments(arguments=arguments)
    )
    installer: Installer = Installer(project=Path(__file__).parent)
    editors: tuple[Editor, ...] = installer.discover()
    if cast(typ="bool", val=parsed.list):
        for editor in editors:
            _ = stdout.write(
                f"{Constants.bold}{Constants.cyan}{editor.identifier}"
                f"{Constants.reset}\t{editor.label}\t"
                f"{Constants.dim}{editor.command or '-'}{Constants.reset}\n",
            )
        return 0
    installer.banner()
    if not editors:
        Installer.report(
            message="No compatible editor installation was detected.",
            color=Constants.red,
            bold=True,
            symbol="✗",
        )
        return 1
    requested: tuple[str, ...] = tuple(
        cast(typ="list[str]", val=parsed.editor),
    )
    selected: tuple[Editor, ...] = (
        tuple(editor for editor in editors if editor.identifier in requested)
        if requested
        else installer.select(editors=editors)
    )
    if requested and len(selected) != len(set(requested)):
        msg: str = "One or more requested editor ids were not detected"
        raise ValueError(msg)
    for editor in selected:
        if cast(typ="bool", val=parsed.uninstall):
            installer.uninstall(editor=editor)
        else:
            installer.install(editor=editor)
    Installer.report(
        message=f"Done — {len(selected)} editor(s) processed.",
        color=Constants.green,
        bold=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
