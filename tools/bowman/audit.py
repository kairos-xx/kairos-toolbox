"""Tamper-evident hash-chained audit records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from json import dumps
from threading import Lock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditEvent:
    """Fields describing one audited event.

    Attributes:
        timestamp: Event timestamp text.
        request_id: Originating request identifier.
        trace_id: Originating trace identifier.
        principal: Acting principal, when known.
        action: Performed action.
        outcome: Event outcome.

    """

    timestamp: str
    request_id: str
    trace_id: str
    principal: str | None
    action: str
    outcome: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditRecord:
    """One chained audit record.

    Attributes:
        sequence: Monotonic record sequence.
        timestamp: Event timestamp text.
        request_id: Originating request identifier.
        trace_id: Originating trace identifier.
        principal: Acting principal, when known.
        action: Performed action.
        outcome: Event outcome.
        previous_hash: Hash of the preceding record.
        record_hash: Hash of this record.

    """

    sequence: int
    timestamp: str
    request_id: str
    trace_id: str
    principal: str | None
    action: str
    outcome: str
    previous_hash: str
    record_hash: str


class HashChainAuditLog:
    """Append-only audit log with SHA-256 chain integrity."""

    def __init__(self, *, path: Path) -> None:
        """Create a log writing chained records to a JSONL file.

        Args:
            path: Destination JSONL file path.

        """
        self._path: Path = path
        self._lock: Lock = Lock()
        self._sequence: int = 0
        self._previous_hash: str = "0" * 64

    def append(self, *, event: AuditEvent) -> AuditRecord:
        """Append and return a chained record.

        Args:
            event: Audited event fields.

        Returns:
            The appended chained record.

        """
        with self._lock:
            sequence: int = self._sequence + 1
            material: str = dumps(
                obj={
                    "sequence": sequence,
                    "timestamp": event.timestamp,
                    "request_id": event.request_id,
                    "trace_id": event.trace_id,
                    "principal": event.principal,
                    "action": event.action,
                    "outcome": event.outcome,
                    "previous_hash": self._previous_hash,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            digest: str = sha256(
                data=material.encode(encoding="utf-8"),
            ).hexdigest()
            record: AuditRecord = AuditRecord(
                sequence=sequence,
                timestamp=event.timestamp,
                request_id=event.request_id,
                trace_id=event.trace_id,
                principal=event.principal,
                action=event.action,
                outcome=event.outcome,
                previous_hash=self._previous_hash,
                record_hash=digest,
            )
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open(mode="a", encoding="utf-8") as stream:
                _ = stream.write(
                    dumps(obj=asdict(obj=record), separators=(",", ":"))
                    + "\n",
                )
            self._sequence = sequence
            self._previous_hash = digest
            return record
