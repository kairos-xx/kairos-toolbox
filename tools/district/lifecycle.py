# Copyright (c) 2026 Project contributors.
"""Authoritative shell command lifecycle instrumentation."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from shlex import quote
from typing import TYPE_CHECKING

from .core import SessionRepository
from .events import (
    DistrictEvent,
    EventDetails,
    EventTypes,
)
from .identity import IdentityStore
from .privacy import PrivacyPolicy, Redactor
from .recorder import EventRecorder
from .storage import DistrictDatabase

if TYPE_CHECKING:
    from pathlib import Path

    from .models import SessionEntry


class CommandLifecycle:
    """Record exact shell command boundaries into the event database."""

    def __init__(self, *, home: Path) -> None:
        """Initialize lifecycle storage.

        Args:
            home: Home.

        """
        self._home: Path = home
        self._repository: SessionRepository = SessionRepository(home=home)
        self._database: DistrictDatabase = DistrictDatabase(home=home)
        self.recorder: EventRecorder = EventRecorder(home=home)
        self._host_id: str = IdentityStore(home=home).host_id()

    def start(
        self,
        *,
        session_id: str,
        command: str,
        cwd: str,
        command_id: str = "",
    ) -> str:
        """Record an authoritative command start and return its UUID.

        Args:
            session_id: Session id.
            command: Command.
            cwd: Cwd.
            command_id: Caller-supplied identifier, such as a tool-use id,
                so a later process can finish the same command.

        Returns:
            The resulting value.

        """
        entry: SessionEntry = self._entry(session_id=session_id)
        command_id = command_id or IdentityStore.new_id()
        started_at: str = datetime.now(tz=UTC).isoformat()
        redacted: str = Redactor.redact(value=command)
        self._database.command_started(
            command_id=command_id,
            session_uuid=entry.uuid,
            session_id=entry.id,
            host_id=entry.host_id or self._host_id,
            agent=entry.agent,
            agent_session=entry.agent_session,
            surface=entry.surface,
            editor=entry.editor,
            command=redacted,
            cwd=cwd,
            started_at=started_at,
        )
        self._update_entry(
            entry=entry,
            current_command=redacted,
            current_command_id=command_id,
            state="EXECUTING",
            activity=started_at,
        )
        self.recorder.record(
            event=DistrictEvent.create(
                event_type=EventTypes.command_started,
                host_id=self._host_id,
                details=EventDetails(
                    session_uuid=entry.uuid,
                    session_id=entry.id,
                    command_id=command_id,
                    agent=entry.agent,
                    agent_session=entry.agent_session,
                    state="EXECUTING",
                    cwd=cwd,
                    command=redacted,
                    surface=entry.surface,
                    editor=entry.editor,
                ),
            ),
        )
        return command_id

    def finish(
        self,
        *,
        session_id: str,
        command_id: str,
        exit_code: int,
        output: str = "",
    ) -> None:
        """Record authoritative completion for a previously started command.

        Args:
            session_id: Session id.
            command_id: Command id.
            exit_code: Exit code.
            output: Captured command output, when the provider reports it.
                It is kept only when the privacy policy persists output, and
                then redacted and limited to the policy's size.

        """
        entry: SessionEntry = self._entry(session_id=session_id)
        finished_at: datetime = datetime.now(tz=UTC)
        started_at: str = self._database.command_started_at(
            command_id=command_id,
        )
        duration: float = 0.0
        if started_at:
            try:
                duration = max(
                    0.0,
                    (
                        finished_at - datetime.fromisoformat(started_at)
                    ).total_seconds(),
                )
            except ValueError:
                duration = 0.0
        self._database.command_finished(
            command_id=command_id,
            finished_at=finished_at.isoformat(),
            exit_code=exit_code,
            duration_seconds=duration,
            output=self._kept(output=output),
        )
        self._update_entry(
            entry=entry,
            current_command="",
            current_command_id="",
            state="IDLE",
            activity=finished_at.isoformat(),
        )
        self.recorder.record(
            event=DistrictEvent.create(
                event_type=EventTypes.command_finished,
                host_id=self._host_id,
                details=EventDetails(
                    session_uuid=entry.uuid,
                    session_id=entry.id,
                    command_id=command_id,
                    agent=entry.agent,
                    agent_session=entry.agent_session,
                    state="IDLE",
                    cwd=entry.cwd,
                    exit_code=exit_code,
                    duration_seconds=duration,
                    surface=entry.surface,
                    editor=entry.editor,
                ),
            ),
        )

    def _kept(self, *, output: str) -> str:
        """Apply the privacy policy to command output before it is stored.

        Args:
            output: Output as the provider reported it.

        Returns:
            Redacted output cut to the policy's limit, or nothing when the
            policy does not persist output.

        """
        policy: PrivacyPolicy = PrivacyPolicy.load(home=self._home)
        if not policy.persist_output or not output:
            return ""
        redacted: str = Redactor.redact(
            value=output,
            extra_patterns=policy.redact_patterns,
        )
        return redacted[-policy.output_limit_bytes :]

    def _entry(self, *, session_id: str) -> SessionEntry:
        """Resolve a session row without mutating shell state.

        Args:
            session_id: Session id.

        Returns:
            The resulting value.

        Raises:
            KeyError: When the operation cannot complete.

        """
        for entry in self._repository.read():
            if entry.id == session_id:
                return entry
        raise KeyError(session_id)

    def _update_entry(
        self,
        *,
        entry: SessionEntry,
        current_command: str,
        current_command_id: str,
        state: str,
        activity: str,
    ) -> None:
        """Synchronize lifecycle state back to the canonical session store.

        Args:
            entry: Entry.
            current_command: Current command.
            current_command_id: Current command id.
            state: State.
            activity: Activity.

        """
        updated: SessionEntry = replace(
            entry,
            current_command=current_command,
            current_command_id=current_command_id,
            agent_state=state,
            last_activity_at=activity,
        )
        _ = self._repository.update(
            transform=lambda entries: tuple(
                updated if current.id == entry.id else current
                for current in entries
            ),
        )


class LifecycleInstaller:
    """Install idempotent zsh/bash hooks for managed District Terminal shells.

    Attributes:
        begin: Begin.
        end: End.

    """

    begin: str = "# >>> district command lifecycle >>>"
    end: str = "# <<< district command lifecycle <<<"

    @classmethod
    def block(cls, *, command: str = "district") -> str:
        """Return a shell-compatible lifecycle instrumentation block.

        Args:
            command: Command.

        Returns:
            The resulting value.

        """
        invocation: str = quote(s=command)
        return f"""\
{cls.begin}
__district_command_id=''
__district_preexec() {{
  [ -n "${{DISTRICT_SESSION_ID:-}}" ] || return 0
  [ -z "${{__district_lifecycle_guard:-}}" ] || return 0
  __district_lifecycle_guard=1
  __district_command_id=$(
    {invocation} --lifecycle-start -- "$1" 2>/dev/null || true
  )
  unset __district_lifecycle_guard
}}
__district_precmd() {{
  __district_rc=$?
  [ -n "${{DISTRICT_SESSION_ID:-}}" ] || return 0
  [ -n "${{__district_command_id:-}}" ] || return 0
  __district_lifecycle_guard=1
  {invocation} \
    --lifecycle-finish \
    "${{__district_command_id}}" \
    "${{__district_rc}}" >/dev/null 2>&1 || true
  __district_command_id=''
  unset __district_lifecycle_guard
}}
if [ -n "${{ZSH_VERSION:-}}" ]; then
  autoload -Uz add-zsh-hook 2>/dev/null || true
  add-zsh-hook preexec __district_preexec
  add-zsh-hook precmd __district_precmd
elif [ -n "${{BASH_VERSION:-}}" ]; then
  trap '__district_preexec "$BASH_COMMAND"' DEBUG
  PROMPT_COMMAND="__district_precmd${{PROMPT_COMMAND:+;$PROMPT_COMMAND}}"
fi
{cls.end}
"""

    @classmethod
    def powershell_block(cls, *, command: str = "district") -> str:
        """Return PowerShell lifecycle hooks for managed District sessions.

        Args:
            command: Command.

        Returns:
            The resulting value.

        """
        invocation: str = (
            '"'
            + command.replace("`", "``").replace(
                '"',
                '`"',
            )
            + '"'
        )
        return f"""\
{cls.begin}
$global:__districtCommandId = ''
$global:__districtOriginalPrompt = ${{function:prompt}}
function global:PSConsoleHostReadLine {{
  $line = [Microsoft.PowerShell.PSConsoleReadLine]::ReadLine()
  if (
    $env:DISTRICT_SESSION_ID `
    -and $line `
    -and -not $global:__districtLifecycleGuard
  ) {{
    $global:__districtLifecycleGuard = $true
    try {{
      $global:__districtCommandId = (
        & {invocation} --lifecycle-start -- $line 2>$null
      )
    }} finally {{
      $global:__districtLifecycleGuard = $false
    }}
  }}
  return $line
}}
function global:prompt {{
  $sharedSucceeded = $?
  $sharedNativeExit = $LASTEXITCODE
  if (
    $env:DISTRICT_SESSION_ID `
    -and $global:__districtCommandId `
    -and -not $global:__districtLifecycleGuard
  ) {{
    $global:__districtLifecycleGuard = $true
    $sharedExit = if ($sharedSucceeded) {{
      if ($null -ne $sharedNativeExit) {{
        $sharedNativeExit
      }} else {{
        0
      }}
    }} else {{
      if ($null -ne $sharedNativeExit) {{
        $sharedNativeExit
      }} else {{
        1
      }}
    }}
    try {{
      & {invocation} `
        --lifecycle-finish `
        $global:__districtCommandId `
        $sharedExit *> $null
    }} finally {{
      $global:__districtCommandId = ''
      $global:__districtLifecycleGuard = $false
    }}
  }}
  if ($null -ne $global:__districtOriginalPrompt) {{
    & $global:__districtOriginalPrompt
  }} else {{
    'PS ' + $(Get-Location) + '> '
  }}
}}
{cls.end}
"""

    @classmethod
    def remove_file(cls, *, path: Path) -> None:
        """Remove District's owned lifecycle block from one startup file.

        Args:
            path: Path.

        """
        if not path.is_file():
            return
        existing: str = path.read_text(encoding="utf-8")
        start: int = existing.find(cls.begin)
        finish: int = existing.find(cls.end)
        if start < 0 or finish < start:
            return
        finish += len(cls.end)
        cleaned: str = (
            existing[:start].rstrip() + "\n" + existing[finish:].lstrip("\n")
        ).strip()
        _unused_result_308: int = path.write_text(
            data=(cleaned + "\n") if cleaned else "",
            encoding="utf-8",
        )

    @classmethod
    def install_powershell(
        cls,
        *,
        path: Path,
        command: str = "district",
    ) -> None:
        """Install/update the owned lifecycle block in a PowerShell profile.

        Args:
            path: Path.
            command: Command.

        """
        existing: str = (
            path.read_text(encoding="utf-8") if path.is_file() else ""
        )
        start: int = existing.find(cls.begin)
        finish: int = existing.find(cls.end)
        if start >= 0 and finish >= start:
            finish += len(cls.end)
            existing = (
                existing[:start].rstrip()
                + "\n"
                + existing[finish:].lstrip("\n")
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        _unused_result_328: int = path.write_text(
            data=existing.rstrip()
            + "\n\n"
            + cls.powershell_block(command=command),
            encoding="utf-8",
        )

    @classmethod
    def install_file(
        cls,
        *,
        path: Path,
        command: str = "district",
    ) -> None:
        """Install/update the owned hook block in one shell startup file.

        Args:
            path: Path.
            command: Command.

        """
        existing: str = (
            path.read_text(encoding="utf-8") if path.is_file() else ""
        )
        start: int = existing.find(cls.begin)
        finish: int = existing.find(cls.end)
        if start >= 0 and finish >= start:
            finish += len(cls.end)
            existing = (
                existing[:start].rstrip()
                + "\n"
                + existing[finish:].lstrip("\n")
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        _unused_result_348: int = path.write_text(
            data=existing.rstrip() + "\n\n" + cls.block(command=command),
            encoding="utf-8",
        )
