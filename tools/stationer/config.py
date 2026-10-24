"""TOML configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tomllib import loads
from typing import Final, cast

from tools.stationer.models import Project
from tools.stationer.versioning import Version, read_version

type Scalar = str | int | float | bool | None
type TomlValue = Scalar | list["TomlValue"] | dict[str, "TomlValue"]
type TomlTable = dict[str, TomlValue]


@dataclass(frozen=True, slots=True, kw_only=True)
class RepositoryConfig:
    """Remote repository settings.

    Attributes:
        provider: Repository host provider name.
        owner: Repository owner or organization.
        name: Repository name.
        default_branch: Default branch name.
        visibility: Repository visibility such as ``private``.
        description: Repository description text.

    """

    provider: str
    owner: str
    name: str
    default_branch: str = "main"
    visibility: str = "private"
    description: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ReleaseConfig:
    """Release conventions.

    Attributes:
        tag_template: Per-project tag format string.
        root_tag_template: Repository-level tag format string.
        pre_hooks: Commands run before each release.
        post_hooks: Commands run after each release.
        publish_pypi: Whether distributions are uploaded to PyPI.
        create_github_release: Whether a GitHub release is created.

    """

    tag_template: str = "{project}/v{version}"
    root_tag_template: str = "v{version}"
    pre_hooks: tuple[tuple[str, ...], ...] = ()
    post_hooks: tuple[tuple[str, ...], ...] = ()
    publish_pypi: bool = True
    create_github_release: bool = True


@dataclass(frozen=True, slots=True, kw_only=True)
class Config:
    """Complete release configuration.

    Attributes:
        root: Directory containing the configuration file.
        repository: Remote repository settings.
        release: Release conventions.
        projects: Configured projects.

    """

    root: Path
    repository: RepositoryConfig
    release: ReleaseConfig
    projects: tuple[Project, ...]

    def project(self, *, name: str) -> Project:
        """Return one configured project.

        Args:
            name: Project name to look up.

        Returns:
            The matching project.

        Raises:
            KeyError: If ``name`` is not a configured project.

        """
        if (
            project := next(
                (item for item in self.projects if item.name == name),
                None,
            )
        ) is None:
            msg: str = f"Unknown project: {name}"
            raise KeyError(msg)
        return project


def _table(*, value: TomlValue | None, name: str) -> TomlTable:
    """Validate that a TOML value is a table.

    Args:
        value: TOML value to validate.
        name: Human-readable field name for error messages.

    Returns:
        The value as a TOML table.

    Raises:
        TypeError: If ``value`` is not a TOML table.

    """
    if not isinstance(value, dict):
        msg: str = f"{name} must be a TOML table"
        raise TypeError(msg)
    return cast(typ="TomlTable", val=value)


def _string(*, table: TomlTable, key: str, default: str | None = None) -> str:
    """Read a required string field from a TOML table.

    Args:
        table: TOML table to read.
        key: Field name.
        default: Value returned when the field is absent.

    Returns:
        The field's string value.

    Raises:
        TypeError: If the field is not a string.

    """
    value: TomlValue | None = table.get(key, default)
    if not isinstance(value, str):
        msg: str = f"{key} must be a string"
        raise TypeError(msg)
    return value


def _boolean(*, table: TomlTable, key: str, default: bool) -> bool:
    """Read a boolean field from a TOML table.

    Args:
        table: TOML table to read.
        key: Field name.
        default: Value returned when the field is absent.

    Returns:
        The field's boolean value.

    Raises:
        TypeError: If the field is not a boolean.

    """
    value: TomlValue = table.get(key, default)
    if not isinstance(value, bool):
        msg: str = f"{key} must be a boolean"
        raise TypeError(msg)
    return value


def _strings(*, table: TomlTable, key: str) -> tuple[str, ...]:
    """Read an array-of-strings field from a TOML table.

    Args:
        table: TOML table to read.
        key: Field name.

    Returns:
        The field's values as a tuple.

    Raises:
        TypeError: If the field is not an array of strings.

    """
    value: TomlValue = table.get(key, [])
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        msg: str = f"{key} must be an array of strings"
        raise TypeError(msg)
    return tuple(cast(typ="list[str]", val=value))


def _hooks(*, table: TomlTable, key: str) -> tuple[tuple[str, ...], ...]:
    """Read an array-of-command-arrays field from a TOML table.

    Args:
        table: TOML table to read.
        key: Field name.

    Returns:
        The hook commands as tuples of arguments.

    Raises:
        TypeError: If the field is not an array of string arrays.

    """
    value: TomlValue = table.get(key, [])
    if not isinstance(value, list):
        msg: str = f"{key} must be an array of command arrays"
        raise TypeError(msg)
    commands: list[tuple[str, ...]] = []
    for command in value:
        if not isinstance(command, list) or not all(
            isinstance(part, str) for part in command
        ):
            msg = f"{key} entries must be arrays of strings"
            raise TypeError(msg)
        commands.append(tuple(cast(typ="list[str]", val=command)))
    return tuple(commands)


def _project_version(
    *,
    root: Path,
    name: str,
    table: TomlTable,
    files: tuple[str, ...],
) -> str:
    """Resolve a project's version from the field or its version files.

    Args:
        root: Repository root the version files are relative to.
        name: Project name used in error messages.
        table: The project's TOML table.
        files: ``version_files`` entries relative to ``root``.

    Returns:
        The project version.

    Raises:
        TypeError: If a present ``version`` field is not a string.
        ValueError: If no version can be resolved.

    """
    raw: TomlValue | None = table.get("version")
    if raw is not None:
        if not isinstance(raw, str):
            msg: str = "version must be a string"
            raise TypeError(msg)
        return raw
    for item in files:
        candidate: Path = root / item
        if candidate.is_file():
            found: str | None = read_version(
                text=candidate.read_text(encoding="utf-8"),
            )
            if found is not None:
                return found
    msg = f"Project {name} needs a version or a readable version file"
    raise ValueError(msg)


def load_config(*, path: Path) -> Config:
    """Load and validate release configuration.

    Args:
        path: Path of the ``release.toml`` file.

    Returns:
        The validated configuration.

    Raises:
        TypeError: If a field carries a value of the wrong type.
        ValueError: If a field carries an invalid value.

    """
    root: Path = path.resolve().parent
    raw: TomlTable = cast(
        typ="TomlTable",
        val=loads(path.read_text(encoding="utf-8")),
    )
    repo: TomlTable = _table(value=raw.get("repository"), name="repository")
    rel: TomlTable = _table(value=raw.get("release", {}), name="release")
    project_tables: TomlTable = _table(
        value=raw.get("projects"),
        name="projects",
    )
    projects: list[Project] = []
    for name, value in project_tables.items():
        table: TomlTable = _table(value=value, name=f"projects.{name}")
        files: tuple[str, ...] = _strings(table=table, key="version_files")
        version: str = _project_version(
            root=root,
            name=name,
            table=table,
            files=files,
        )
        Version.parse(value=version)
        projects.append(
            Project(
                name=name,
                path=Path(_string(table=table, key="path", default=".")),
                version=version,
                version_files=tuple(Path(item) for item in files),
                python_distribution=_boolean(
                    table=table,
                    key="python_distribution",
                    default=False,
                ),
                distribution_name=_string(
                    table=table,
                    key="distribution_name",
                    default=name,
                ),
                depends_on=_strings(table=table, key="depends_on"),
                artifacts=_strings(table=table, key="artifacts"),
            ),
        )
    config: Config = Config(
        root=root,
        repository=RepositoryConfig(
            provider=_string(table=repo, key="provider", default="github"),
            owner=_string(table=repo, key="owner"),
            name=_string(table=repo, key="name"),
            default_branch=_string(
                table=repo,
                key="default_branch",
                default="main",
            ),
            visibility=_string(
                table=repo,
                key="visibility",
                default="private",
            ),
            description=_string(table=repo, key="description", default=""),
        ),
        release=ReleaseConfig(
            tag_template=_string(
                table=rel,
                key="tag_template",
                default="{project}/v{version}",
            ),
            root_tag_template=_string(
                table=rel,
                key="root_tag_template",
                default="v{version}",
            ),
            pre_hooks=_hooks(table=rel, key="pre_hooks"),
            post_hooks=_hooks(table=rel, key="post_hooks"),
            publish_pypi=_boolean(table=rel, key="publish_pypi", default=True),
            create_github_release=_boolean(
                table=rel,
                key="create_github_release",
                default=True,
            ),
        ),
        projects=tuple(projects),
    )
    _validate_dependencies(config=config)
    return config


def _validate_dependencies(*, config: Config) -> None:
    """Check that every declared dependency names a configured project.

    Args:
        config: Configuration to validate.

    Raises:
        ValueError: If a project depends on an unknown project.

    """
    names: Final[frozenset[str]] = frozenset(
        project.name for project in config.projects
    )
    for project in config.projects:
        unknown: set[str] = set(project.depends_on) - names
        if unknown:
            msg: str = (
                f"{project.name} has unknown dependencies: {sorted(unknown)}"
            )
            raise ValueError(msg)
