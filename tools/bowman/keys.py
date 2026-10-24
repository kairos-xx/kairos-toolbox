"""Local signing key ring and rotation."""

from __future__ import annotations

from base64 import urlsafe_b64encode
from dataclasses import dataclass, field
from enum import StrEnum
from hmac import digest, new
from os import environ
from pathlib import Path
from secrets import token_bytes
from typing import Final, final

from .errors import BowmanError


@final
class Constants:
    """Group module-level constants.

    Attributes:
        key_environment: Grouped module constant.
        default_key_id: Grouped module constant.
        default_key_path: Grouped module constant.
        encoding: Grouped module constant.
        key_bytes: Grouped module constant.

    """

    key_environment: Final[str] = "BOWMAN_URI_KEY"
    default_key_id: Final[str] = "primary"
    default_key_path: Final[str] = ".uriapi/keys/keyring.hex"
    encoding: Final[str] = "utf-8"
    key_bytes: Final[int] = 32


class KeyStatus(StrEnum):
    """Signing key lifecycle state.

    Attributes:
        ACTIVE: Key may sign and verify.
        RETIRING: Key may verify but not sign.
        REVOKED: Key may neither sign nor verify.

    """

    ACTIVE = "active"
    RETIRING = "retiring"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True, kw_only=True)
class SigningKey:
    """One signing key version.

    Attributes:
        key_id: Unique key identifier.
        material: Raw HMAC key material.
        status: Lifecycle state.

    """

    key_id: str
    material: bytes
    status: KeyStatus = KeyStatus.ACTIVE


@dataclass(slots=True, kw_only=True)
class KeyRing:
    """Rotating HMAC key ring with one active signing key."""

    _keys: dict[str, SigningKey] = field(default_factory=dict)
    _active_id: str | None = None

    def __init__(self, *, keys: dict[str, SigningKey] | None = None) -> None:
        """Create an empty ring or one seeded from keys.

        Args:
            keys: Optional initial signing keys.

        """
        self._keys = dict(keys or {})
        self._active_id = None

    @classmethod
    def load(
        cls,
        *,
        path: Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> KeyRing:
        """Load the primary key from env or disk, generating if needed.

        Args:
            path: Optional on-disk key file path.
            environment: Optional environment mapping override.

        Returns:
            A key ring containing one active primary key.

        """
        env: dict[str, str] = environment or dict(environ)
        material: str | None = env.get(Constants.key_environment)
        if material is None:
            material = cls._load_or_generate(path=path)
        ring: KeyRing = cls(
            keys={
                Constants.default_key_id: SigningKey(
                    key_id=Constants.default_key_id,
                    material=material.encode(encoding=Constants.encoding),
                ),
            },
        )
        ring._active_id = Constants.default_key_id
        return ring

    def rotate(self, *, key_id: str) -> SigningKey:
        """Activate a fresh key and retire the previous active key.

        Args:
            key_id: Identifier for the new active key.

        Returns:
            The newly activated key.

        """
        current: SigningKey | None = self.get(key_id=self._active_id)
        material: bytes = token_bytes(nbytes=Constants.key_bytes)
        new_key: SigningKey = SigningKey(
            key_id=key_id,
            material=material,
            status=KeyStatus.ACTIVE,
        )
        self._keys[key_id] = new_key
        self._active_id = key_id
        if current is not None:
            self._keys[current.key_id] = SigningKey(
                key_id=current.key_id,
                material=current.material,
                status=KeyStatus.RETIRING,
            )
        return new_key

    def get(self, *, key_id: str | None = None) -> SigningKey | None:
        """Return a key by id or the active key.

        Args:
            key_id: Key identifier, or `None` for the active key.

        Returns:
            The requested key, or `None` when missing.

        """
        if key_id is None:
            key_id = self._active_id
        return self._keys.get(key_id) if key_id else None

    def revoke(self, *, key_id: str) -> None:
        """Mark a key revoked.

        Args:
            key_id: Key identifier to revoke.

        """
        current: SigningKey | None = self._keys.get(key_id)
        if current is None:
            return
        self._keys[key_id] = SigningKey(
            key_id=current.key_id,
            material=current.material,
            status=KeyStatus.REVOKED,
        )

    def sign(self, *, payload: bytes) -> tuple[str, str]:
        """Sign payload with the active key.

        Args:
            payload: Bytes to sign.

        Returns:
            Key identifier and base64 signature.

        Raises:
            BowmanError: When no active key exists.

        """
        key: SigningKey | None = self.get()
        if key is None:
            msg: str = "KeyRing has no active key."
            raise BowmanError(msg)
        signature: str = urlsafe_b64encode(
            s=digest(key=key.material, msg=payload, digest="sha256"),
        ).decode(encoding="ascii")
        return (key.key_id, signature)

    def verify(self, *, key_id: str, payload: bytes, signature: str) -> bool:
        """Verify a signature with the matching non-revoked key.

        Args:
            key_id: Identifier of the signing key.
            payload: Signed bytes.
            signature: Expected base64 signature.

        Returns:
            Whether the signature verifies.

        """
        key: SigningKey | None = self._keys.get(key_id)
        if key is None or key.status is KeyStatus.REVOKED:
            return False
        expected: str = urlsafe_b64encode(
            s=digest(key=key.material, msg=payload, digest="sha256"),
        ).decode(encoding="ascii")
        return (
            new(
                key=key.material,
                msg=expected.encode(encoding="ascii"),
                digestmod="sha256",
            ).hexdigest()
            == new(
                key=key.material,
                msg=signature.encode(encoding="ascii"),
                digestmod="sha256",
            ).hexdigest()
        )

    @staticmethod
    def _load_or_generate(*, path: Path | None) -> str:
        """Load key text from disk or generate it.

        Args:
            path: Optional explicit key file path.

        Returns:
            The hex-encoded key material.

        """
        home: Path | None = path
        if home is None:
            home = Path.home() / Constants.default_key_path
        if home.exists():
            return home.read_text(encoding=Constants.encoding).strip()
        home.parent.mkdir(parents=True, exist_ok=True)
        generated: str = token_bytes(nbytes=Constants.key_bytes).hex()
        home.write_text(data=generated, encoding=Constants.encoding)
        return generated
