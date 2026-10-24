"""Signed request/response protocol envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from json import dumps
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .json_types import JSONValue
    from .keys import KeyRing


@dataclass(frozen=True, slots=True, kw_only=True)
class SignedEnvelope:
    """Canonical signed protocol envelope.

    Attributes:
        version: Envelope format version.
        key_id: Identifier of the signing key.
        payload: Canonical signed JSON payload.
        signature: Base64 signature over the payload.

    """

    version: int
    key_id: str
    payload: str
    signature: str

    def verify(self, *, keys: KeyRing) -> bool:
        """Verify this envelope against its key ring.

        Args:
            keys: Key ring holding the signing keys.

        Returns:
            Whether the signature verifies.

        """
        return keys.verify(
            key_id=self.key_id,
            payload=self.payload.encode(encoding="utf-8"),
            signature=self.signature,
        )


class EnvelopeSigner:
    """Canonical JSON envelope signer."""

    @staticmethod
    def sign(
        *,
        keys: KeyRing,
        values: Mapping[str, JSONValue],
        version: int = 1,
    ) -> SignedEnvelope:
        """Canonicalize and sign values.

        Args:
            keys: Key ring used to sign.
            values: Values to canonicalize.
            version: Envelope format version.

        Returns:
            The signed envelope.

        """
        payload: str = dumps(
            obj=dict(values),
            separators=(",", ":"),
            sort_keys=True,
        )
        key_id: str
        signature: str
        key_id, signature = keys.sign(payload=payload.encode(encoding="utf-8"))
        return SignedEnvelope(
            version=version,
            key_id=key_id,
            payload=payload,
            signature=signature,
        )

    @staticmethod
    def digest(*, values: Mapping[str, JSONValue]) -> str:
        """Return a canonical argument/request digest.

        Args:
            values: Values to canonicalize and hash.

        Returns:
            Hex digest of the canonical encoding.

        """
        payload: str = dumps(
            obj=dict(values),
            separators=(",", ":"),
            sort_keys=True,
        )
        return sha256(data=payload.encode(encoding="utf-8")).hexdigest()
