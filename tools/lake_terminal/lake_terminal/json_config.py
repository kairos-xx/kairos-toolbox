# Copyright (c) 2026 Project contributors.
"""Read and write third-party JSON configuration without destroying it."""

from __future__ import annotations

from json import JSONDecodeError, dumps, loads
from pathlib import Path
from shutil import copy2
from stat import S_IMODE
from tempfile import NamedTemporaryFile
from typing import Final, cast

type JsonValue = (
    bool
    | int
    | float
    | str
    | list["JsonValue"]
    | dict[str, "JsonValue"]
    | None
)
type JsonObject = dict[str, JsonValue]


class JsonConfig:
    """Load and atomically save a user-owned JSON object file.

    Attributes:
        backup_suffix: Suffix of the one-time pre-modification backup.

    """

    backup_suffix: Final[str] = ".lake-bak"

    @classmethod
    def save(cls, *, path: Path, data: JsonObject) -> None:
        """Atomically replace ``path``, backing up the original once.

        Args:
            path: Configuration file.
            data: Object to store.

        """
        path.parent.mkdir(parents=True, exist_ok=True)
        mode: int | None = (
            S_IMODE(path.stat().st_mode) if path.is_file() else None
        )
        backup: Path = path.with_name(name=path.name + cls.backup_suffix)
        if path.is_file() and not backup.exists():
            _ = copy2(src=path, dst=backup)
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary: Path = Path(handle.name)
            _ = handle.write(dumps(obj=data, indent=2) + "\n")
        if mode is not None:
            temporary.chmod(mode=mode)
        _ = temporary.replace(target=path)

    @staticmethod
    def load(*, path: Path) -> JsonObject:
        """Return the JSON object stored at ``path``.

        Args:
            path: Configuration file.

        Returns:
            The object, or an empty object when the file is absent or empty.

        Raises:
            ValueError: When the file is not valid JSON.
            TypeError: When the file holds JSON that is not an object. Callers
                therefore never overwrite content they cannot understand.

        """
        if not path.is_file():
            return {}
        text: str = path.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        try:
            payload: JsonValue = cast(typ="JsonValue", val=loads(s=text))
        except JSONDecodeError as error:
            msg: str = f"{path} is not valid JSON; refusing to modify it."
            raise ValueError(msg) from error
        if not isinstance(payload, dict):
            msg = f"{path} must contain a JSON object; refusing to modify it."
            raise TypeError(msg)
        return payload
