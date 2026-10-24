# Copyright (c) 2026 Project contributors.
"""Persist and publish normalized observability events."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from .events import DistrictEvent, EventTypes
from .ipc import EventPublisher
from .notifications import (
    NotificationRules,
    Notifier,
)
from .privacy import PrivacyPolicy, Redactor
from .storage import DistrictDatabase

if TYPE_CHECKING:
    from pathlib import Path


class EventRecorder:
    """Write events transactionally and publish them to a running daemon."""

    def __init__(self, *, home: Path) -> None:
        """Initialize event persistence and IPC.

        Args:
            home: Home.

        """
        self._database: DistrictDatabase = DistrictDatabase(home=home)
        self._publisher: EventPublisher = EventPublisher(home=home)
        self._policy: PrivacyPolicy = PrivacyPolicy.load(home=home)
        self._notification_rules = NotificationRules(home=home).read()

    def record(self, *, event: DistrictEvent) -> None:
        """Persist a redacted event before best-effort live publication.

        Args:
            event: Event.

        """
        live_event: DistrictEvent = replace(
            event,
            command=Redactor.redact(
                value=event.command,
                extra_patterns=self._policy.redact_patterns,
            ),
            payload=Redactor.redact(
                value=event.payload,
                extra_patterns=self._policy.redact_patterns,
            ),
        )
        persisted: DistrictEvent = (
            replace(live_event, payload="")
            if live_event.event_type == EventTypes.output_activity
            and not self._policy.persist_output
            else live_event
        )
        self._database.append_event(event=persisted)
        _unused_result_59: bool = self._publisher.publish(
            payload=live_event.json(),
        )
        _ = Notifier.for_event(
            event=live_event,
            rules=self._notification_rules,
        )
