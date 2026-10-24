# Copyright (c) 2026 Project contributors.
"""Build PyPI distributions (sdist + wheel) for a project and its siblings.

``pip install`` resolves every declared dependency from PyPI, so a project
whose manifest lists ``local_dependencies`` can only be published once those
siblings are published too. ``PypiBuilder`` builds them first, from
throwaway copies, then the project itself — the checkout stays clean. The
PyPA ``build`` tool runs from a throwaway virtual environment in isolated
mode (``-I``): the default output folder is called ``build``, and from the
repository root ``python -m build`` would otherwise import that folder.
"""

from __future__ import annotations

from os import name as os_name
from pathlib import Path
from sys import executable
from sys import stdout as sys_stdout
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Final, final

from tools.conjure import QemuRunnerError
from tools.stationer.builder import (
    ProjectConfig,
    SourceStaging,
    job_failure,
)
from tools.stationer.sessions import HostConstants, open_host

if TYPE_CHECKING:
    from tools.stationer.sessions import HostOpener, JobSession


@final
class PypiBuilder:
    """Build sdist+wheel pairs for ``local_dependencies`` then the project.

    Dependencies come first so a caller publishing the directory publishes
    resolvable metadata for everything the project declares.
    """

    def __init__(self, *, open_host: HostOpener = open_host) -> None:
        """Initialize the builder.

        Args:
            open_host: Creates the host session commands run in.

        """
        self._open_host: Final[HostOpener] = open_host

    def build(
        self,
        *,
        project: ProjectConfig,
        output: Path,
    ) -> tuple[Path, ...]:
        """Build every distribution into ``output``.

        Each ``local_dependencies`` entry is built before the project so a
        sorted upload publishes the dependency graph in order. Builds run on
        staged copies — ``build/`` and ``*.egg-info`` never appear in the
        source checkout — with ``build`` installed in a fresh virtual
        environment, so the host needs no preinstalled build tooling.

        Args:
            project: Project configuration.
            output: Directory that receives the ``.whl``/``.tar.gz`` files.

        Returns:
            The distribution paths produced, dependencies first.

        Raises:
            job_failure: When a host command fails.

        """
        output.mkdir(parents=True, exist_ok=True)
        built: list[Path] = []
        host: JobSession = self._open_host()
        try:
            host.start()
            with TemporaryDirectory(
                prefix="stationer-pypi-",
            ) as temporary:
                dependencies, source = SourceStaging.stage(
                    project=project,
                    directory=Path(temporary) / "staged",
                )
                python: Path = self._tooling(
                    host=host,
                    directory=Path(temporary) / "venv",
                )
                for root in (*dependencies, source):
                    destination: Path = output / (
                        project.name if root == source else root.name
                    )
                    destination.mkdir(parents=True, exist_ok=True)
                    known: set[Path] = set(destination.iterdir())
                    _ = host.run(
                        command=(
                            str(object=python),
                            "-I",
                            "-m",
                            "build",
                            "--outdir",
                            str(object=destination),
                            str(object=root),
                        ),
                        check=True,
                        timeout_seconds=HostConstants.timeout_seconds,
                    )
                    built.extend(
                        sorted(
                            path
                            for path in set(destination.iterdir()) - known
                            if path.suffix in {".whl", ".gz"}
                        ),
                    )
        except QemuRunnerError as error:
            raise job_failure(error=error) from error
        finally:
            host.close()
        distributions: tuple[Path, ...] = tuple(built)
        for path in distributions:
            _ = sys_stdout.write(f"{path}\n")
        return distributions

    @staticmethod
    def _tooling(*, host: JobSession, directory: Path) -> Path:
        """Create a virtual environment that provides the ``build`` tool.

        Every command runs in isolated mode (``-I``) and pip runs with
        ``--isolated`` so no ``PYTHON*`` or ``PIP_*`` environment variable
        can redirect the install away from the environment the build
        calls then use.

        Args:
            host: Session the commands run in.
            directory: Where the environment is created.

        Returns:
            The environment's Python interpreter.

        """
        _ = host.run(
            command=(
                executable,
                "-I",
                "-m",
                "venv",
                str(object=directory),
            ),
            check=True,
            timeout_seconds=HostConstants.timeout_seconds,
        )
        python: Path = directory / (
            "Scripts/python.exe" if os_name == "nt" else "bin/python"
        )
        _ = host.run(
            command=(
                str(object=python),
                "-I",
                "-m",
                "pip",
                "--isolated",
                "install",
                "--quiet",
                "build",
            ),
            check=True,
            timeout_seconds=HostConstants.timeout_seconds,
        )
        _ = host.run(
            command=(str(object=python), "-I", "-m", "build", "--version"),
            check=True,
            timeout_seconds=HostConstants.timeout_seconds,
        )
        return python
