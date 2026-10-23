# Copyright (c) 2026 Project contributors.
"""Native Windows ConPTY sessions owned by the Lake Terminal daemon."""

from __future__ import annotations

from ctypes import (
    CDLL,
    POINTER,
    Array,
    Structure,
    byref,
    c_char,
    c_int16,
    c_size_t,
    c_void_p,
    c_wchar,
    create_string_buffer,
    create_unicode_buffer,
    sizeof,
    wintypes,
)
from ctypes import __dict__ as ctypes_symbols
from ctypes import (
    cast as ctypes_cast,
)
from dataclasses import dataclass
from importlib import import_module
from json import dumps, loads
from os import name as os_name
from subprocess import list2cmdline
from sys import stdin, stdout
from threading import Lock, Thread
from time import sleep
from typing import TYPE_CHECKING, ClassVar, Final, Protocol, cast

from .buffer import OutputRingBuffer
from .ipc import EventServer

if TYPE_CHECKING:
    from pathlib import Path

    from _typeshed import Incomplete

    from .privacy import PrivacyPolicy
type JsonValue = (
    bool
    | int
    | float
    | str
    | list["JsonValue"]
    | dict[str, "JsonValue"]
    | None
)


class _KernelCall(Protocol):
    """Describe a positional native kernel function."""

    def __call__(self, *args: Incomplete) -> Incomplete:
        """Invoke the native function.

        Args:
            *args: Native positional arguments.

        Returns:
            Native call result.

        """
        ...


def _kernel_call(*, kernel32: CDLL, name: str) -> _KernelCall:
    """Return a typed native kernel function.

    Args:
        kernel32: Loaded kernel32 library.
        name: Native function name.

    Returns:
        Typed positional native function.

    """
    return cast(typ="_KernelCall", val=getattr(kernel32, name))


class _WinDllFactory(Protocol):
    """Describe the Windows DLL loader callable."""

    def __call__(
        self,
        name: str,
        *,
        use_last_error: bool = False,
    ) -> CDLL:
        """Load one Windows DLL.

        Args:
            name: DLL name.
            use_last_error: Whether to preserve the Windows last-error value.

        Returns:
            Loaded DLL.

        """
        ...


class _GetWideChar(Protocol):
    """Describe ``msvcrt.getwch``."""

    def __call__(self) -> str:
        """Read one wide character.

        Returns:
            Read character.

        """
        ...


class _KeyHit(Protocol):
    """Describe ``msvcrt.kbhit``."""

    def __call__(self) -> int:
        """Return whether keyboard input is waiting.

        Returns:
            Nonzero when input is available.

        """
        ...


class _Msvcrt(Protocol):
    """Describe the subset of ``msvcrt`` used by the ConPTY client.

    Attributes:
        getwch: Wide-character keyboard read.
        kbhit: Non-blocking key-availability probe.

    """

    getwch: _GetWideChar
    kbhit: _KeyHit


def last_error() -> int:
    """Return the Windows thread-local error code when available.

    Returns:
        Windows error code, or zero when unavailable.

    """
    getter: object = ctypes_symbols.get("get_last_error")
    if callable(getter):
        result: object = getter()
        return result if isinstance(result, int) else 0
    return 0


def win_dll_factory() -> _WinDllFactory | None:
    """Return the Windows DLL loader when available.

    Returns:
        Windows DLL loader, or None on unsupported hosts.

    """
    value: object = ctypes_symbols.get("WinDLL")
    if not callable(value):
        return None
    return cast(typ="_WinDllFactory", val=value)


class _Coord(Structure):
    """Windows COORD structure."""

    _fields_: ClassVar[Incomplete] = (
        ("X", c_int16),
        ("Y", c_int16),
    )


class _StartupInfo(Structure):
    """Windows STARTUPINFOW structure."""

    _fields_: ClassVar[Incomplete] = (
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", POINTER(wintypes.BYTE)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    )


class _StartupInfoEx(Structure):
    """Windows STARTUPINFOEXW structure."""

    _fields_: ClassVar[Incomplete] = (
        ("StartupInfo", _StartupInfo),
        ("lpAttributeList", c_void_p),
    )


class _ProcessInformation(Structure):
    """Windows PROCESS_INFORMATION structure."""

    _fields_: ClassVar[Incomplete] = (
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class ConPtyCapabilities:
    """Report whether native ConPTY APIs are available.

    Attributes:
        available: Whether native ConPTY APIs are available.
        reason: Reason the APIs are unavailable.

    """

    available: bool
    reason: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ConPtyInfo:
    """Serializable daemon-owned pseudo-console metadata.

    Attributes:
        session_uuid: Stable session UUID.
        pid: Child process identifier.
        alive: Whether the child process is alive.

    """

    session_uuid: str
    pid: int
    alive: bool


class WindowsConPty:
    """One native Windows pseudo-console and its child shell process.

    Attributes:
        proc_thread_attribute_pseudoconsole: Pseudo-console attribute value.
        extended_startupinfo_present: Extended startup-information flag.
        wait_timeout: Windows wait timeout result.

    """

    proc_thread_attribute_pseudoconsole: Final[int] = 0x00020016
    extended_startupinfo_present: Final[int] = 0x00080000
    wait_timeout: Final[int] = 0x00000102

    def __init__(
        self,
        *,
        hpc: int,
        input_write: int,
        output_read: int,
        process_handle: int,
        pid: int,
    ) -> None:
        """Store native handles created by :meth:`spawn`.

        Args:
            hpc: Pseudo-console handle.
            input_write: Input pipe write handle.
            output_read: Output pipe read handle.
            process_handle: Child process handle.
            pid: Child process identifier.

        """
        self._hpc: int = hpc
        self._input_write: int = input_write
        self._output_read: int = output_read
        self._process_handle: int = process_handle
        self.pid: int = pid
        self._closed: bool = False

    @classmethod
    def spawn(
        cls,
        *,
        command: tuple[str, ...],
        cwd: str,
        columns: int = 120,
        rows: int = 40,
    ) -> WindowsConPty:
        """Create a native pseudo-console and child process.

        Args:
            command: Child command and arguments.
            cwd: Child working directory.
            columns: Initial pseudo-console columns.
            rows: Initial pseudo-console rows.

        Returns:
            Created pseudo-console.

        Raises:
            OSError: When the pseudo-console cannot be created.

        """
        if not cls.capabilities().available:
            msg: str = "Windows ConPTY is not available on this host."
            raise OSError(msg)
        kernel32: CDLL = cls.kernel32()
        input_read: wintypes.HANDLE = wintypes.HANDLE()
        input_write: wintypes.HANDLE = wintypes.HANDLE()
        output_read: wintypes.HANDLE = wintypes.HANDLE()
        output_write: wintypes.HANDLE = wintypes.HANDLE()
        if not kernel32.CreatePipe(
            *(byref(*(input_read,)), byref(*(input_write,)), None, 0),
        ):
            raise OSError(
                last_error(),
                "CreatePipe(input) failed",
            )
        if not kernel32.CreatePipe(
            *(byref(*(output_read,)), byref(*(output_write,)), None, 0),
        ):
            _ = kernel32.CloseHandle(*(input_read,))
            _ = kernel32.CloseHandle(*(input_write,))
            raise OSError(
                last_error(),
                "CreatePipe(output) failed",
            )
        hpc: c_void_p = c_void_p()
        hr: int = int(
            kernel32.CreatePseudoConsole(
                *(
                    _Coord(columns, rows),
                    input_read,
                    output_write,
                    0,
                    byref(*(hpc,)),
                ),
            ),
        )
        _ = kernel32.CloseHandle(*(input_read,))
        _ = kernel32.CloseHandle(*(output_write,))
        if hr != 0 or hpc.value is None:
            _ = kernel32.CloseHandle(*(input_write,))
            _ = kernel32.CloseHandle(*(output_read,))
            raise OSError(
                hr,
                "CreatePseudoConsole failed",
            )
        size: c_size_t = c_size_t()
        _ = kernel32.InitializeProcThreadAttributeList(
            *(None, 1, 0, byref(*(size,))),
        )
        attribute_buffer: Array[c_char] = create_string_buffer(
            init=size.value,
        )
        attribute_list: c_void_p = ctypes_cast(
            obj=attribute_buffer,
            typ=c_void_p,
        )
        if not kernel32.InitializeProcThreadAttributeList(
            *(attribute_list, 1, 0, byref(*(size,))),
        ):
            _kernel_call(kernel32=kernel32, name="ClosePseudoConsole")(hpc)
            raise OSError(
                last_error(),
                "InitializeProcThreadAttributeList failed",
            )
        if not _kernel_call(
            kernel32=kernel32,
            name="UpdateProcThreadAttribute",
        )(
            attribute_list,
            0,
            cls.proc_thread_attribute_pseudoconsole,
            hpc,
            sizeof(c_void_p),
            None,
            None,
        ):
            _ = _kernel_call(
                kernel32=kernel32,
                name="DeleteProcThreadAttributeList",
            )(attribute_list)
            _kernel_call(kernel32=kernel32, name="ClosePseudoConsole")(hpc)
            raise OSError(
                last_error(),
                "UpdateProcThreadAttribute failed",
            )
        startup: _StartupInfoEx = _StartupInfoEx()
        startup.StartupInfo.cb = sizeof(_StartupInfoEx)
        startup.lpAttributeList = attribute_list
        info: _ProcessInformation = _ProcessInformation()
        command_line: Array[c_wchar] = create_unicode_buffer(
            init=list2cmdline(seq=command),
        )
        created: int = kernel32.CreateProcessW(
            *(
                None,
                command_line,
                None,
                None,
                False,
                cls.extended_startupinfo_present,
                None,
                cwd or None,
                byref(*(startup,)),
                byref(*(info,)),
            ),
        )
        _ = _kernel_call(
            kernel32=kernel32,
            name="DeleteProcThreadAttributeList",
        )(attribute_list)
        if not created:
            _kernel_call(kernel32=kernel32, name="ClosePseudoConsole")(hpc)
            _ = kernel32.CloseHandle(*(input_write,))
            _ = kernel32.CloseHandle(*(output_read,))
            raise OSError(
                last_error(),
                "CreateProcessW failed",
            )
        _ = kernel32.CloseHandle(*(info.hThread,))
        return WindowsConPty(
            hpc=hpc.value or 0,
            input_write=input_write.value or 0,
            output_read=output_read.value or 0,
            process_handle=info.hProcess or 0,
            pid=info.dwProcessId,
        )

    @staticmethod
    def capabilities() -> ConPtyCapabilities:
        """Return whether the native pseudo-console API can be used.

        Returns:
            Native ConPTY capability information.

        """
        if os_name != "nt":
            return ConPtyCapabilities(
                available=False,
                reason="not-windows",
            )
        factory: _WinDllFactory | None = win_dll_factory()
        if factory is None:
            return ConPtyCapabilities(
                available=False,
                reason="conpty-api-unavailable",
            )
        try:
            kernel32: CDLL = factory(
                name="kernel32",
                use_last_error=True,
            )
            for symbol_name in (
                "CreatePseudoConsole",
                "ResizePseudoConsole",
                "ClosePseudoConsole",
                "InitializeProcThreadAttributeList",
                "UpdateProcThreadAttribute",
            ):
                _ = getattr(kernel32, symbol_name)
        except (AttributeError, OSError):
            return ConPtyCapabilities(
                available=False,
                reason="conpty-api-unavailable",
            )
        return ConPtyCapabilities(available=True)

    @staticmethod
    def kernel32() -> CDLL:
        """Return the Windows kernel32 DLL only on Windows.

        Returns:
            Loaded kernel32 DLL.

        Raises:
            OSError: When the Windows DLL loader is unavailable.

        """
        factory: _WinDllFactory | None = win_dll_factory()
        if factory is None:
            msg: str = "Windows DLL loading is unavailable on this host."
            raise OSError(msg)
        return factory(
            name="kernel32",
            use_last_error=True,
        )

    def read(self, *, size: int = 4096) -> bytes:
        """Read output from the pseudo-console pipe.

        Args:
            size: Maximum number of bytes to read.

        Returns:
            Bytes read from the pseudo-console.

        """
        kernel32: CDLL = self.kernel32()
        buffer: Array[c_char] = (c_char * size)()
        read: wintypes.DWORD = wintypes.DWORD()
        success: int = kernel32.ReadFile(
            *(
                wintypes.HANDLE(self._output_read),
                buffer,
                size,
                byref(*(read,)),
                None,
            ),
        )
        if not success or read.value == 0:
            return b""
        return bytes(buffer[: read.value])

    def write(self, *, data: bytes) -> int:
        """Write input bytes to the pseudo-console.

        Args:
            data: Input bytes.

        Returns:
            Number of bytes written.

        Raises:
            OSError: When the write fails.

        """
        kernel32: CDLL = self.kernel32()
        written: wintypes.DWORD = wintypes.DWORD()
        buffer: Array[c_char] = create_string_buffer(init=data)
        success: int = kernel32.WriteFile(
            *(
                wintypes.HANDLE(self._input_write),
                buffer,
                len(data),
                byref(*(written,)),
                None,
            ),
        )
        if not success:
            raise OSError(
                last_error(),
                "WriteFile failed",
            )
        return written.value

    def resize(self, *, columns: int, rows: int) -> None:
        """Resize the pseudo-console.

        Args:
            columns: New column count.
            rows: New row count.

        Raises:
            OSError: When the resize fails.

        """
        result: int = int(
            _kernel_call(
                kernel32=self.kernel32(),
                name="ResizePseudoConsole",
            )(
                c_void_p(self._hpc),
                _Coord(columns, rows),
            ),
        )
        if result != 0:
            raise OSError(
                result,
                "ResizePseudoConsole failed",
            )

    def alive(self) -> bool:
        """Return whether the child process remains live.

        Returns:
            Whether the child process is still running.

        """
        result: int = int(
            _kernel_call(
                kernel32=self.kernel32(),
                name="WaitForSingleObject",
            )(
                wintypes.HANDLE(self._process_handle),
                0,
            ),
        )
        return result == self.wait_timeout

    def terminate(self) -> None:
        """Terminate the child and release pseudo-console handles."""
        if self._closed:
            return
        kernel32: CDLL = self.kernel32()
        if self.alive():
            _ = _kernel_call(
                kernel32=kernel32,
                name="TerminateProcess",
            )(
                wintypes.HANDLE(self._process_handle),
                1,
            )
        _kernel_call(
            kernel32=kernel32,
            name="ClosePseudoConsole",
        )(c_void_p(self._hpc))
        _ = kernel32.CloseHandle(
            *(wintypes.HANDLE(self._input_write),),
        )
        _ = kernel32.CloseHandle(
            *(wintypes.HANDLE(self._output_read),),
        )
        _ = kernel32.CloseHandle(
            *(wintypes.HANDLE(self._process_handle),),
        )
        self._closed = True


@dataclass(slots=True, kw_only=True)
class _ManagedConPty:
    """Daemon-owned pseudo-console plus its bounded output buffer.

    Attributes:
        session: Native pseudo-console.
        buffer: Bounded live output buffer.
        lock: Output-buffer lock.
        command: Command used to create the top-level shell.
        cwd: Working directory used to create the shell.
        closing: Whether the managed pseudo-console is shutting down.

    """

    session: WindowsConPty
    buffer: OutputRingBuffer
    lock: Lock
    command: tuple[str, ...]
    cwd: str
    closing: bool = False


class ConPtyManager:
    """Own persistent Windows pseudo-consoles inside the Lake daemon."""

    def __init__(self, *, policy: PrivacyPolicy) -> None:
        """Initialize an empty daemon-local session map.

        Args:
            policy: Privacy policy.

        """
        self._policy: PrivacyPolicy = policy
        self.sessions: dict[str, _ManagedConPty] = {}

    def create(
        self,
        *,
        session_uuid: str,
        command: tuple[str, ...],
        cwd: str,
    ) -> ConPtyInfo:
        """Create and begin draining one persistent pseudo-console.

        Args:
            session_uuid: Stable session UUID.
            command: Top-level shell command.
            cwd: Working directory.

        Returns:
            Pseudo-console metadata.

        """
        existing: _ManagedConPty | None = self.sessions.get(
            session_uuid,
        )
        if existing is not None and existing.session.alive():
            return ConPtyInfo(
                session_uuid=session_uuid,
                pid=existing.session.pid,
                alive=True,
            )
        session: WindowsConPty = WindowsConPty.spawn(
            command=command,
            cwd=cwd,
        )
        managed: _ManagedConPty = _ManagedConPty(
            session=session,
            buffer=OutputRingBuffer(
                limit_bytes=self._policy.output_limit_bytes,
            ),
            lock=Lock(),
            command=command,
            cwd=cwd,
        )
        self.sessions[session_uuid] = managed
        _ = Thread(
            target=self.drain,
            kwargs={"session_uuid": session_uuid},
            daemon=True,
        ).start()
        return ConPtyInfo(
            session_uuid=session_uuid,
            pid=session.pid,
            alive=True,
        )

    def output(self, *, session_uuid: str) -> str:
        """Return bounded live output for one pseudo-console.

        Args:
            session_uuid: Stable session UUID.

        Returns:
            Current bounded output.

        """
        managed: _ManagedConPty | None = self.sessions.get(
            session_uuid,
        )
        if managed is None:
            return ""
        with managed.lock:
            return managed.buffer.text()

    def write(self, *, session_uuid: str, text: str) -> int:
        """Write textual input to one pseudo-console.

        Args:
            session_uuid: Stable session UUID.
            text: Input text.

        Returns:
            Number of bytes written.

        Raises:
            KeyError: When the pseudo-console is unavailable.

        """
        managed: _ManagedConPty | None = self.sessions.get(
            session_uuid,
        )
        if managed is None or not managed.session.alive():
            raise KeyError(session_uuid)
        return managed.session.write(data=text.encode())

    def kill(self, *, session_uuid: str) -> bool:
        """Terminate and forget one pseudo-console.

        Args:
            session_uuid: Stable session UUID.

        Returns:
            Whether a managed pseudo-console existed.

        """
        managed: _ManagedConPty | None = self.sessions.pop(
            session_uuid,
            None,
        )
        if managed is None:
            return False
        managed.closing = True
        managed.session.terminate()
        return True

    def info(self, *, session_uuid: str) -> ConPtyInfo | None:
        """Return daemon metadata for one pseudo-console.

        Args:
            session_uuid: Stable session UUID.

        Returns:
            Pseudo-console metadata when the session exists.

        """
        managed: _ManagedConPty | None = self.sessions.get(
            session_uuid,
        )
        if managed is None:
            return None
        return ConPtyInfo(
            session_uuid=session_uuid,
            pid=managed.session.pid,
            alive=managed.session.alive(),
        )

    def drain(self, *, session_uuid: str) -> None:
        """Drain output and respawn the top-level shell after normal exit.

        Args:
            session_uuid: Stable session UUID.

        """
        managed: _ManagedConPty | None = self.sessions.get(
            session_uuid,
        )
        if managed is None:
            return
        while not managed.closing:
            while managed.session.alive() and not managed.closing:
                try:
                    data: bytes = managed.session.read()
                except OSError:
                    break
                if not data:
                    sleep(0.02)
                    continue
                text: str = data.decode(errors="replace")
                with managed.lock:
                    managed.buffer.append(text=text)
            if managed.closing:
                return
            try:
                managed.session.terminate()
                managed.session = WindowsConPty.spawn(
                    command=managed.command,
                    cwd=managed.cwd,
                )
            except OSError:
                return
            sleep(0.2)


class ConPtyClient:
    """IPC client for daemon-owned Windows pseudo-console sessions."""

    def __init__(self, *, home: Path) -> None:
        """Store the daemon endpoint home.

        Args:
            home: User home directory.

        """
        self._home: Path = home

    def create(
        self,
        *,
        session_uuid: str,
        cwd: str,
        command: tuple[str, ...],
    ) -> ConPtyInfo:
        """Create a persistent daemon-owned pseudo-console.

        Args:
            session_uuid: Stable session UUID.
            cwd: Working directory.
            command: Top-level shell command.

        Returns:
            Created pseudo-console metadata.

        Raises:
            OSError: When the daemon response is invalid.

        """
        response: str = EventServer.request(
            home=self._home,
            operation="conpty-create",
            values={
                "session_uuid": session_uuid,
                "cwd": cwd,
                "command": dumps(obj=command),
            },
        )
        parsed: JsonValue = cast(
            typ="JsonValue",
            val=loads(s=response),
        )
        if not isinstance(parsed, dict):
            msg: str = "Invalid ConPTY daemon response."
            raise OSError(msg)
        pid_value: JsonValue = parsed.get("pid")
        alive_value: JsonValue = parsed.get("alive")
        if isinstance(pid_value, bool) or not isinstance(
            pid_value,
            (int, str),
        ):
            msg = "Invalid ConPTY daemon PID."
            raise OSError(msg)
        if not isinstance(alive_value, bool):
            msg = "Invalid ConPTY daemon alive state."
            raise OSError(msg)
        try:
            pid: int = int(pid_value)
        except ValueError as error:
            msg = "Invalid ConPTY daemon PID."
            raise OSError(msg) from error
        return ConPtyInfo(
            session_uuid=session_uuid,
            pid=pid,
            alive=alive_value,
        )

    def output(self, *, session_uuid: str) -> str:
        """Return daemon-buffered pseudo-console output.

        Args:
            session_uuid: Stable session UUID.

        Returns:
            Current bounded output.

        """
        return EventServer.request(
            home=self._home,
            operation="conpty-output",
            values={"session_uuid": session_uuid},
        )

    def write(self, *, session_uuid: str, text: str) -> int:
        """Write input through the daemon.

        Args:
            session_uuid: Stable session UUID.
            text: Input text.

        Returns:
            Number of bytes written.

        """
        response: str = EventServer.request(
            home=self._home,
            operation="conpty-write",
            values={
                "session_uuid": session_uuid,
                "text": text,
            },
        )
        return int(response)

    def kill(self, *, session_uuid: str) -> bool:
        """Terminate a daemon-owned pseudo-console.

        Args:
            session_uuid: Stable session UUID.

        Returns:
            Whether the daemon terminated the pseudo-console.

        """
        return (
            EventServer.request(
                home=self._home,
                operation="conpty-kill",
                values={"session_uuid": session_uuid},
            )
            == "OK"
        )

    def alive(self, *, session_uuid: str) -> bool:
        """Return whether a daemon-owned pseudo-console is live.

        Args:
            session_uuid: Stable session UUID.

        Returns:
            Whether the pseudo-console is alive.

        """
        return (
            EventServer.request(
                home=self._home,
                operation="conpty-alive",
                values={"session_uuid": session_uuid},
            )
            == "1"
        )

    def attach(self, *, session_uuid: str) -> int:
        """Interact with a daemon-owned ConPTY; Ctrl-] detaches.

        Args:
            session_uuid: Stable session UUID.

        Returns:
            Exit status.

        """
        if os_name != "nt" or not stdin.isatty():
            output: str = self.output(
                session_uuid=session_uuid,
            )
            if output:
                _ = stdout.write(output)
            return 0
        msvcrt: _Msvcrt = cast(
            typ="_Msvcrt",
            val=import_module(name="msvcrt"),
        )
        shown: str = ""
        while self.alive(session_uuid=session_uuid):
            output = self.output(
                session_uuid=session_uuid,
            )
            if output != shown:
                delta: str = output.removeprefix(shown)
                if delta:
                    _ = stdout.write(delta)
                    stdout.flush()
                shown = output
            if msvcrt.kbhit():
                key: str = msvcrt.getwch()
                if key == "\x1d":
                    return 0
                if key in {"\x00", "\xe0"} and msvcrt.kbhit():
                    code: str = msvcrt.getwch()
                    sequence: str = {
                        "H": "\x1b[A",
                        "P": "\x1b[B",
                        "K": "\x1b[D",
                        "M": "\x1b[C",
                    }.get(code, "")
                    if sequence:
                        _ = self.write(
                            session_uuid=session_uuid,
                            text=sequence,
                        )
                else:
                    _ = self.write(
                        session_uuid=session_uuid,
                        text=key,
                    )
            sleep(0.02)
        return 0
