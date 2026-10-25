# Copyright (c) 2026 Project contributors.
"""Content-addressed packaging of project source for remote builds.

``SourcePacks`` turns the owned project tree into a partition catalog and
zstd packs inside a persistent store. Because the store survives builds,
unchanged blocks are packed once and every guest, container and future
run reuses them: transfer to a builder is only ever the packs that
builder has not seen before.
"""

from __future__ import annotations

from pathlib import Path
from shutil import copytree, ignore_patterns
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Final, final
from zipfile import ZIP_DEFLATED, ZipFile

from tools.stationer.partition.store import (
    ObjectStore,
    PartitionStore,
    Recipe,
    RecipeScanner,
    destination,
    load_catalog,
)

if TYPE_CHECKING:
    from tools.stationer.builder import ProjectConfig
    from tools.stationer.partition.store import Catalog


@final
class Constants:
    """Group module-level constants.

    Attributes:
        component: Grouped module constant.
        runtime: Grouped module constant.
        ignored: Grouped module constant.
        pack_target: Grouped module constant.

    """

    component: Final[str] = "source"
    runtime: Final[str] = "partition-runtime.zip"
    ignored: Final[tuple[str, ...]] = (
        ".git",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "*.egg-info",
        ".DS_Store",
    )
    pack_target: Final[int] = 64 * 1024


class SourcePacks:
    """Catalog project source into a reusable content-addressed store."""

    @staticmethod
    def create(*, project: ProjectConfig, store: Path) -> Path:
        """Stage, scan and pack the project source into ``store``.

        Args:
            project: Project configuration.
            store: Persistent partition store; shared between builds.

        Returns:
            The catalog path; packs and objects live beside it.

        """
        temporary_text: str
        with TemporaryDirectory(prefix="stationer-packs-") as temporary_text:
            staged: Path = Path(temporary_text) / "src"
            _ = copytree(
                src=project.root,
                dst=staged,
                ignore=ignore_patterns(*Constants.ignored),
            )
            for dependency in project.local_dependencies:
                _ = copytree(
                    src=dependency,
                    dst=staged / ".local-deps" / dependency.name,
                    ignore=ignore_patterns(*Constants.ignored),
                )
            partition: PartitionStore = PartitionStore(cache=store / "cache")
            objects: ObjectStore = partition.objects
            scanner: RecipeScanner = RecipeScanner(objects=objects)
            recipe: Recipe = scanner.scan(
                root=staged,
                name=Constants.component,
                metadata={},
            )
            _ = partition.build(
                recipes={Constants.component: recipe},
                output=store / "dist",
                platform="source",
                pack_target=Constants.pack_target,
            )
        return store / "dist" / "catalog.json"

    @staticmethod
    def packs(*, store: Path) -> dict[str, Path]:
        """Map every pack's store-relative path to its file.

        Args:
            store: Partition store previously filled by ``create``.

        Returns:
            ``packs/...`` store-relative path to its absolute file.

        """
        catalog: Catalog = load_catalog(path=store / "dist" / "catalog.json")
        root: Path = store / "dist"
        return {
            record["path"]: destination(root=root, relative=record["path"])
            for record in catalog["packs"].values()
        }

    @staticmethod
    def bootstrap(*, destination: Path) -> None:
        """Write a self-contained ``stationer.partition`` runtime zip.

        A builder only needs the package itself plus its vendored zstd
        payload; the archive is uploaded once per remote store.

        Args:
            destination: Zip path to create.

        """
        root: Path = Path(__file__).parent
        archive: ZipFile
        with ZipFile(
            file=destination,
            mode="w",
            compression=ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            archive.write(
                filename=root / "__init__.py",
                arcname="stationer/__init__.py",
            )
            partition: Path = root / "partition"
            for path in sorted(partition.rglob(pattern="*")):
                if not path.is_file():
                    continue
                relative: Path = path.relative_to(partition)
                if any(part in Constants.ignored for part in relative.parts):
                    continue
                archive.write(
                    filename=path,
                    arcname=Path("stationer/partition") / relative,
                )
