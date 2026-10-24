# Copyright (c) 2026 Project contributors.
"""Typed tmux status-line configuration, format DSL, and CLI renderer."""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum
from shlex import join
from sys import stdout
from typing import Final, Self

_MAX_COLOUR_INDEX: Final[int] = 255
_RGB_HEX_LENGTH: Final[int] = 7
_CONTROL_RANGE_MAX: Final[int] = 9
_USER_RANGE_MAX_BYTES: Final[int] = 15


class Scope(StrEnum):
    """tmux option scopes."""

    SESSION = "session"
    WINDOW = "window"
    PANE = "pane"
    SERVER = "server"


class StatusPosition(StrEnum):
    """Status-line positions."""

    TOP = "top"
    BOTTOM = "bottom"


class StatusJustify(StrEnum):
    """Window-list justification."""

    LEFT = "left"
    CENTRE = "centre"
    RIGHT = "right"
    ABSOLUTE_CENTRE = "absolute-centre"


class StatusKeys(StrEnum):
    """Status prompt key modes."""

    EMACS = "emacs"
    VI = "vi"


class ColourName(StrEnum):
    """Named tmux colours."""

    DEFAULT = "default"
    TERMINAL = "terminal"
    BLACK = "black"
    RED = "red"
    GREEN = "green"
    YELLOW = "yellow"
    BLUE = "blue"
    MAGENTA = "magenta"
    CYAN = "cyan"
    WHITE = "white"
    BRIGHTBLACK = "brightblack"
    BRIGHTRED = "brightred"
    BRIGHTGREEN = "brightgreen"
    BRIGHTYELLOW = "brightyellow"
    BRIGHTBLUE = "brightblue"
    BRIGHTMAGENTA = "brightmagenta"
    BRIGHTCYAN = "brightcyan"
    BRIGHTWHITE = "brightwhite"


class Align(StrEnum):
    """Inline style alignment."""

    LEFT = "left"
    CENTRE = "centre"
    RIGHT = "right"


class ListMarker(StrEnum):
    """status-format window-list markers."""

    ON = "on"
    FOCUS = "focus"
    LEFT_MARKER = "left-marker"
    RIGHT_MARKER = "right-marker"


class RangeKind(StrEnum):
    """Mouse-sensitive status ranges."""

    LEFT = "left"
    RIGHT = "right"
    SESSION = "session"
    WINDOW = "window"
    PANE = "pane"
    USER = "user"
    CONTROL = "control"


@dataclass(frozen=True, slots=True, kw_only=True)
class Colour:
    """Represent a tmux colour."""

    value: str

    def __post_init__(self) -> None:
        """Validate the colour.

        Raises:
            ValueError: If the colour is invalid.

        """
        named: Final[frozenset[str]] = frozenset(
            item.value for item in ColourName
        )
        valid_index: bool = (
            self.value.startswith("colour")
            and self.value[6:].isdigit()
            and 0 <= int(self.value[6:]) <= _MAX_COLOUR_INDEX
        )
        valid_rgb: bool = (
            len(self.value) == _RGB_HEX_LENGTH
            and self.value.startswith("#")
            and all(
                char in "0123456789abcdefABCDEF" for char in self.value[1:]
            )
        )
        if self.value not in named and not valid_index and not valid_rgb:
            msg: str = f"invalid tmux colour: {self.value}"
            raise ValueError(msg)

    @classmethod
    def indexed(cls, *, index: int) -> Self:
        """Create a 256-colour value.

        Args:
            index: Palette index from 0 through 255.

        Returns:
            Colour instance.

        Raises:
            ValueError: If index is outside the supported range.

        """
        if not 0 <= index <= _MAX_COLOUR_INDEX:
            msg: str = "colour index must be between 0 and 255"
            raise ValueError(msg)
        return cls(value=f"colour{index}")

    @classmethod
    def rgb(cls, *, red: int, green: int, blue: int) -> Self:
        """Create an RGB colour.

        Args:
            red: Red channel.
            green: Green channel.
            blue: Blue channel.

        Returns:
            Colour instance.

        Raises:
            ValueError: If a channel is outside 0 through 255.

        """
        channels: tuple[int, int, int] = (red, green, blue)
        if any(not 0 <= channel <= _MAX_COLOUR_INDEX for channel in channels):
            msg: str = "RGB channels must be between 0 and 255"
            raise ValueError(msg)
        return cls(value=f"#{red:02x}{green:02x}{blue:02x}")


@dataclass(frozen=True, slots=True, kw_only=True)
class TmuxRange:
    """Represent a tmux inline mouse range."""

    kind: RangeKind
    value: str | int | None = None

    def render(self) -> str:
        """Render the range.

        Returns:
            tmux range style fragment.

        Raises:
            ValueError: If the range value is invalid for its kind.

        """
        if self.kind in {RangeKind.LEFT, RangeKind.RIGHT}:
            if self.value is not None:
                msg: str = "left/right ranges do not accept values"
                raise ValueError(msg)
            return f"range={self.kind.value}"
        if self.value is None:
            msg = f"{self.kind.value} range requires a value"
            raise ValueError(msg)
        if self.kind is RangeKind.CONTROL:
            if (
                not isinstance(self.value, int)
                or not 0 <= self.value <= _CONTROL_RANGE_MAX
            ):
                msg = "control range must be an integer from 0 to 9"
                raise ValueError(msg)
        elif isinstance(self.value, int):
            msg = "only control ranges accept integer values"
            raise ValueError(msg)
        elif (
            self.kind is RangeKind.USER
            and len(self.value.encode()) > _USER_RANGE_MAX_BYTES
        ):
            msg = "user range value must be at most 15 bytes"
            raise ValueError(msg)
        return f"range={self.kind.value}|{self.value}"


@dataclass(frozen=True, slots=True, kw_only=True)
class TmuxStyle:
    """Build a tmux style without hand-written style strings."""

    foreground: Colour | None = None
    background: Colour | None = None
    fill: Colour | None = None
    width: int | str | None = None
    align: Align | None = None
    link: str | None = None
    range_: TmuxRange | None = None
    list_marker: ListMarker | None = None
    bold: bool | None = None
    dim: bool | None = None
    underscore: bool | None = None
    blink: bool | None = None
    reverse: bool | None = None
    hidden: bool | None = None
    italics: bool | None = None
    strikethrough: bool | None = None
    noattr: bool = False
    push_default: bool = False
    pop_default: bool = False
    set_default: bool = False

    @staticmethod
    def _flag(*, name: str, value: bool | None) -> str | None:
        """Render an attribute flag.

        Args:
            name: Attribute name.
            value: Attribute state.

        Returns:
            Rendered attribute or None.

        """
        if value is None:
            return None
        return name if value else f"no{name}"

    def _typed_parts(self) -> list[str]:
        """Render colour, layout, range, and list fragments.

        Returns:
            Style fragments for typed fields.

        """
        parts: list[str] = []
        for name, colour in (
            ("fg", self.foreground),
            ("bg", self.background),
            ("fill", self.fill),
        ):
            if colour is not None:
                parts.append(f"{name}={colour.value}")
        if self.width is not None:
            parts.append(f"width={self.width}")
        if self.align is not None:
            parts.append(f"align={self.align.value}")
        if self.link is not None:
            parts.append(f"link={self.link}" if self.link else "nolink")
        if self.range_ is not None:
            parts.append(self.range_.render())
        if self.list_marker is not None:
            parts.append(f"list={self.list_marker.value}")
        return parts

    def _attribute_parts(self) -> list[str]:
        """Render on/off attribute fragments.

        Returns:
            Style fragments for attribute fields.

        """
        attributes: Final[tuple[tuple[str, bool | None], ...]] = (
            ("bold", self.bold),
            ("dim", self.dim),
            ("underscore", self.underscore),
            ("blink", self.blink),
            ("reverse", self.reverse),
            ("hidden", self.hidden),
            ("italics", self.italics),
            ("strikethrough", self.strikethrough),
        )
        parts: list[str] = [
            rendered
            for name, value in attributes
            if (rendered := self._flag(name=name, value=value)) is not None
        ]
        if self.noattr:
            parts.append("noattr")
        return parts

    def _default_parts(self) -> list[str]:
        """Render default-stack modifiers.

        Returns:
            Style fragments for default-stack fields.

        """
        return [
            name
            for name, enabled in (
                ("push-default", self.push_default),
                ("pop-default", self.pop_default),
                ("set-default", self.set_default),
            )
            if enabled
        ]

    def render(self) -> str:
        """Render the style.

        Returns:
            tmux style string.

        """
        parts: list[str] = [
            *self._typed_parts(),
            *self._attribute_parts(),
            *self._default_parts(),
        ]
        return ",".join(parts) if parts else "default"


@dataclass(frozen=True, slots=True, kw_only=True)
class TmuxFormat:
    """Composable tmux format expression."""

    text: str

    def __str__(self) -> str:
        """Return format text.

        Returns:
            Raw tmux format.

        """
        return self.text

    def then(self, *, other: TmuxFormat | str) -> TmuxFormat:
        """Concatenate another format.

        Args:
            other: Format or literal text.

        Returns:
            Combined format.

        """
        return TmuxFormat(text=self.text + TmuxFormat.raw(value=other).text)

    @staticmethod
    def raw(*, value: TmuxFormat | str) -> TmuxFormat:
        """Normalize format input.

        Args:
            value: Existing format or raw tmux text.

        Returns:
            Format instance.

        """
        if isinstance(value, TmuxFormat):
            return value
        return TmuxFormat(text=value)

    @classmethod
    def literal(cls, *, value: str) -> TmuxFormat:
        """Create escaped literal text.

        Args:
            value: Literal text.

        Returns:
            Escaped format.

        """
        return cls(text=value.replace("#", "##"))

    @classmethod
    def variable(cls, *, name: str) -> TmuxFormat:
        """Build a tmux format variable reference.

        Args:
            name: Variable name.

        Returns:
            Variable expression.

        """
        return cls(text=f"#{{{name}}}")

    @classmethod
    def user_option(cls, *, name: str) -> TmuxFormat:
        """Build a tmux user-option reference.

        Args:
            name: Name with or without leading @.

        Returns:
            User-option expression.

        """
        normalized: str = name if name.startswith("@") else f"@{name}"
        return cls.variable(name=normalized)

    @classmethod
    def conditional(
        cls,
        *,
        condition: TmuxFormat | str,
        when_true: TmuxFormat | str,
        when_false: TmuxFormat | str,
    ) -> TmuxFormat:
        """Build a conditional.

        Args:
            condition: tmux condition expression.
            when_true: True branch.
            when_false: False branch.

        Returns:
            Conditional format.

        """
        condition_text: str = cls.raw(value=condition).text
        if condition_text.startswith("#{") and condition_text.endswith("}"):
            condition_text = condition_text[2:-1]
        true_text: str = cls.raw(value=when_true).text
        false_text: str = cls.raw(value=when_false).text
        return cls(
            text=f"#{{?{condition_text},{true_text},{false_text}}}",
        )

    @classmethod
    def operator(
        cls,
        *,
        operator: str,
        left: TmuxFormat | str,
        right: TmuxFormat | str,
    ) -> TmuxFormat:
        """Build a binary tmux format operator.

        Args:
            operator: tmux operator such as ==, !=, &&, ||, <, or >.
            left: Left operand.
            right: Right operand.

        Returns:
            Operator expression.

        """
        left_text: str = cls.raw(value=left).text
        right_text: str = cls.raw(value=right).text
        return cls(text=f"#{{{operator}:{left_text},{right_text}}}")

    @classmethod
    def match(
        cls,
        *,
        pattern: str,
        value: TmuxFormat | str,
        regex: bool = False,
        ignore_case: bool = False,
    ) -> TmuxFormat:
        """Build a tmux match expression.

        Args:
            pattern: Pattern to match.
            value: Value being matched.
            regex: Whether pattern is an extended regular expression.
            ignore_case: Whether matching ignores case.

        Returns:
            Match expression.

        """
        flags: str = "".join(
            ("r" if regex else "", "i" if ignore_case else ""),
        )
        modifier: str = f"/{flags}" if flags else ""
        return cls(
            text=f"#{{m{modifier}:{pattern},{cls.raw(value=value).text}}}",
        )

    @classmethod
    def modified(cls, *, modifiers: str, value: str) -> TmuxFormat:
        """Apply arbitrary tmux format modifiers.

        Args:
            modifiers: Modifier chain before the colon.
            value: Variable or expression body.

        Returns:
            Modified expression.

        """
        return cls(text=f"#{{{modifiers}:{value}}}")

    @classmethod
    def time(
        cls,
        *,
        variable: str,
        relative: bool = False,
        difference: bool = False,
        pretty: bool = False,
        format_: str | None = None,
    ) -> TmuxFormat:
        """Render a tmux timestamp variable.

        Args:
            variable: Time-valued variable.
            relative: Render relative time.
            difference: Render difference in seconds.
            pretty: Use less precise past-time output.
            format_: Optional strftime format.

        Returns:
            Time format expression.

        """
        suffix: str = ""
        if pretty:
            suffix += "/p"
        if relative:
            suffix += "/r"
        if difference:
            suffix += "/d"
        if format_ is not None:
            suffix += f"/f/{format_}"
        return cls(text=f"#{{t{suffix}:{variable}}}")

    @classmethod
    def loop(
        cls,
        *,
        kind: str,
        format_: TmuxFormat | str,
        current_format: TmuxFormat | str | None = None,
        flags: str = "",
    ) -> TmuxFormat:
        """Build a session/window/pane/client/options/environment loop.

        Args:
            kind: Loop prefix such as S, W, P, L, O, or V.
            format_: Normal item format.
            current_format: Optional current/active item format.
            flags: tmux loop flags.

        Returns:
            Loop expression.

        """
        prefix: str = f"{kind}/{flags}" if flags else kind
        body: str = cls.raw(value=format_).text
        if current_format is not None:
            body += f",{cls.raw(value=current_format).text}"
        return cls(text=f"#{{{prefix}:{body}}}")

    @classmethod
    def shell_command(cls, *, command: str) -> TmuxFormat:
        """Insert asynchronous shell-command output.

        Args:
            command: Shell command understood by tmux.

        Returns:
            Shell-command format.

        """
        return cls(text=f"#({command})")

    @classmethod
    def styled(
        cls,
        *,
        value: TmuxFormat | str,
        style: TmuxStyle,
        reset: bool = True,
    ) -> TmuxFormat:
        """Apply an inline style.

        Args:
            value: Styled content.
            style: Style definition.
            reset: Whether to append #[default].

        Returns:
            Styled format.

        """
        tail: str = "#[default]" if reset else ""
        return cls(
            text=f"#[{style.render()}]{cls.raw(value=value).text}{tail}",
        )


class Formats:
    """Common tmux status variables and aliases."""

    SESSION_NAME: Final[TmuxFormat] = TmuxFormat.variable(name="session_name")
    SESSION_ID: Final[TmuxFormat] = TmuxFormat.variable(name="session_id")
    SESSION_WINDOWS: Final[TmuxFormat] = TmuxFormat.variable(
        name="session_windows",
    )
    SESSION_ATTACHED: Final[TmuxFormat] = TmuxFormat.variable(
        name="session_attached",
    )
    WINDOW_INDEX: Final[TmuxFormat] = TmuxFormat.variable(name="window_index")
    WINDOW_NAME: Final[TmuxFormat] = TmuxFormat.variable(name="window_name")
    WINDOW_ID: Final[TmuxFormat] = TmuxFormat.variable(name="window_id")
    WINDOW_FLAGS: Final[TmuxFormat] = TmuxFormat.variable(name="window_flags")
    WINDOW_ACTIVE: Final[TmuxFormat] = TmuxFormat.variable(
        name="window_active",
    )
    WINDOW_ACTIVITY: Final[TmuxFormat] = TmuxFormat.variable(
        name="window_activity",
    )
    WINDOW_BELL: Final[TmuxFormat] = TmuxFormat.variable(
        name="window_bell_flag",
    )
    PANE_INDEX: Final[TmuxFormat] = TmuxFormat.variable(name="pane_index")
    PANE_ID: Final[TmuxFormat] = TmuxFormat.variable(name="pane_id")
    PANE_PID: Final[TmuxFormat] = TmuxFormat.variable(name="pane_pid")
    PANE_COMMAND: Final[TmuxFormat] = TmuxFormat.variable(
        name="pane_current_command",
    )
    PANE_PATH: Final[TmuxFormat] = TmuxFormat.variable(
        name="pane_current_path",
    )
    PANE_ACTIVE: Final[TmuxFormat] = TmuxFormat.variable(name="pane_active")
    PANE_DEAD: Final[TmuxFormat] = TmuxFormat.variable(name="pane_dead")
    PANE_DEAD_STATUS: Final[TmuxFormat] = TmuxFormat.variable(
        name="pane_dead_status",
    )
    PANE_WIDTH: Final[TmuxFormat] = TmuxFormat.variable(name="pane_width")
    PANE_HEIGHT: Final[TmuxFormat] = TmuxFormat.variable(name="pane_height")
    CLIENT_NAME: Final[TmuxFormat] = TmuxFormat.variable(name="client_name")
    CLIENT_TERMNAME: Final[TmuxFormat] = TmuxFormat.variable(
        name="client_termname",
    )
    CLIENT_WIDTH: Final[TmuxFormat] = TmuxFormat.variable(name="client_width")
    CLIENT_HEIGHT: Final[TmuxFormat] = TmuxFormat.variable(
        name="client_height",
    )
    HOST: Final[TmuxFormat] = TmuxFormat.variable(name="host")
    HOST_SHORT: Final[TmuxFormat] = TmuxFormat.variable(name="host_short")
    VERSION: Final[TmuxFormat] = TmuxFormat.variable(name="version")
    SOCKET_PATH: Final[TmuxFormat] = TmuxFormat.variable(name="socket_path")


@dataclass(frozen=True, slots=True, kw_only=True)
class TmuxStatusConfig:
    """Describe status and window-status options."""

    rows: int | bool | None = None
    position: StatusPosition | None = None
    interval_seconds: int | None = None
    justify: StatusJustify | None = None
    keys: StatusKeys | None = None
    left: TmuxFormat | str | None = None
    left_length: int | None = None
    left_style: TmuxStyle | str | None = None
    right: TmuxFormat | str | None = None
    right_length: int | None = None
    right_style: TmuxStyle | str | None = None
    style: TmuxStyle | str | None = None
    window_format: TmuxFormat | str | None = None
    window_current_format: TmuxFormat | str | None = None
    window_style: TmuxStyle | str | None = None
    window_current_style: TmuxStyle | str | None = None
    window_last_style: TmuxStyle | str | None = None
    window_activity_style: TmuxStyle | str | None = None
    window_bell_style: TmuxStyle | str | None = None
    window_silence_style: TmuxStyle | str | None = None
    window_separator: TmuxFormat | str | None = None
    pane_status_format: TmuxFormat | str | None = None
    pane_current_status_format: TmuxFormat | str | None = None
    message_style: TmuxStyle | str | None = None
    message_command_style: TmuxStyle | str | None = None

    def __post_init__(self) -> None:
        """Validate configuration.

        Raises:
            ValueError: If a numeric setting is invalid.

        """
        if (
            isinstance(self.rows, int)
            and not isinstance(self.rows, bool)
            and self.rows not in {2, 3, 4, 5}
        ):
            msg: str = "status rows must be 2, 3, 4, or 5"
            raise ValueError(msg)
        numeric: tuple[int | None, ...] = (
            self.interval_seconds,
            self.left_length,
            self.right_length,
        )
        if any(value is not None and value < 0 for value in numeric):
            msg = "numeric status options cannot be negative"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class TmuxCommand:
    """One safe argv-based tmux command."""

    argv: tuple[str, ...]

    def shell(self) -> str:
        """Render for display.

        Returns:
            Shell-escaped command.

        """
        return join(self.argv)


type TmuxValue = bool | int | str | StrEnum | TmuxFormat | TmuxStyle


@dataclass(frozen=True, slots=True, kw_only=True)
class TmuxOption:
    """Generic option assignment."""

    name: str
    value: TmuxValue
    scope: Scope = Scope.SESSION
    target: str | None = None
    global_: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class TmuxShowFlags:
    """Display modifiers for show-options."""

    value_only: bool = False
    quiet: bool = False
    inherited: bool = False
    hooks: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class TmuxDistrictState:
    """District Terminal session state for @-option updates.

    Attributes:
        agent: ``SessionEntry.agent`` — provider such as claude, codex or
            devin.
        agent_state: ``SessionEntry.agent_state`` — normalized provider
            state such as IDLE, THINKING, EXECUTING, WAITING_FOR_USER or
            DEAD.
        id: ``SessionEntry.id`` — short public session identifier.
        initiator: ``SessionEntry.initiator`` — who started the session.

    """

    agent: str | None = None
    agent_state: str | None = None
    id: str | None = None
    initiator: str | None = None


class TmuxStatusManager:
    """Build all tmux commands needed to manage status presentation."""

    _STATUS_OPTIONS: Final[dict[str, tuple[str, Scope]]] = {
        "rows": ("status", Scope.SESSION),
        "position": ("status-position", Scope.SESSION),
        "interval_seconds": ("status-interval", Scope.SESSION),
        "justify": ("status-justify", Scope.SESSION),
        "keys": ("status-keys", Scope.SESSION),
        "left": ("status-left", Scope.SESSION),
        "left_length": ("status-left-length", Scope.SESSION),
        "left_style": ("status-left-style", Scope.SESSION),
        "right": ("status-right", Scope.SESSION),
        "right_length": ("status-right-length", Scope.SESSION),
        "right_style": ("status-right-style", Scope.SESSION),
        "style": ("status-style", Scope.SESSION),
        "window_format": ("window-status-format", Scope.WINDOW),
        "window_current_format": (
            "window-status-current-format",
            Scope.WINDOW,
        ),
        "window_style": ("window-status-style", Scope.WINDOW),
        "window_current_style": ("window-status-current-style", Scope.WINDOW),
        "window_last_style": ("window-status-last-style", Scope.WINDOW),
        "window_activity_style": (
            "window-status-activity-style",
            Scope.WINDOW,
        ),
        "window_bell_style": ("window-status-bell-style", Scope.WINDOW),
        "window_silence_style": ("window-status-silence-style", Scope.WINDOW),
        "window_separator": ("window-status-separator", Scope.WINDOW),
        "pane_status_format": ("window-pane-status-format", Scope.WINDOW),
        "pane_current_status_format": (
            "window-pane-current-status-format",
            Scope.WINDOW,
        ),
        "message_style": ("message-style", Scope.SESSION),
        "message_command_style": ("message-command-style", Scope.SESSION),
    }

    def __init__(self, *, executable: str = "tmux") -> None:
        """Initialize the manager.

        Args:
            executable: tmux executable or absolute path.

        """
        self._executable: Final[str] = executable

    @staticmethod
    def _render_value(*, value: TmuxValue) -> str:
        """Render an option value.

        Args:
            value: Typed option value.

        Returns:
            tmux-compatible text.

        """
        if isinstance(value, bool):
            return "on" if value else "off"
        if isinstance(value, TmuxFormat):
            return value.text
        if isinstance(value, TmuxStyle):
            return value.render()
        if isinstance(value, StrEnum):
            return value.value
        return str(value)

    @staticmethod
    def _scope_flags(*, scope: Scope, global_: bool) -> tuple[str, ...]:
        """Return set/show scope flags.

        Args:
            scope: Option scope.
            global_: Whether to target global options.

        Returns:
            CLI flags.

        """
        scope_flag: Final[dict[Scope, str]] = {
            Scope.SESSION: "",
            Scope.WINDOW: "-w",
            Scope.PANE: "-p",
            Scope.SERVER: "-s",
        }
        flags: list[str] = []
        if scope_flag[scope]:
            flags.append(scope_flag[scope])
        if global_:
            flags.append("-g")
        return tuple(flags)

    def set_option(
        self,
        *,
        option: TmuxOption,
        append: bool = False,
        only_if_unset: bool = False,
        quiet: bool = False,
        expand: bool = False,
    ) -> TmuxCommand:
        """Build set-option.

        Args:
            option: Option assignment to apply.
            append: Append string/style value.
            only_if_unset: Do not replace an already-set option.
            quiet: Suppress unknown/ambiguous option errors.
            expand: Expand formats in value before setting.

        Returns:
            Command.

        """
        argv: list[str] = [self._executable, "set-option"]
        argv.extend(
            self._scope_flags(scope=option.scope, global_=option.global_),
        )
        if append:
            argv.append("-a")
        if only_if_unset:
            argv.append("-o")
        if quiet:
            argv.append("-q")
        if expand:
            argv.append("-F")
        if option.target is not None:
            argv.extend(("-t", option.target))
        argv.extend((option.name, self._render_value(value=option.value)))
        return TmuxCommand(argv=tuple(argv))

    def unset_option(
        self,
        *,
        option: TmuxOption,
        quiet: bool = False,
        unset_panes: bool = False,
    ) -> TmuxCommand:
        """Build an option reset/unset.

        Args:
            option: Option to reset; its value is ignored.
            quiet: Suppress unknown-option errors.
            unset_panes: Also unset pane values when applicable.

        Returns:
            Command.

        """
        argv: list[str] = [self._executable, "set-option"]
        argv.extend(
            self._scope_flags(scope=option.scope, global_=option.global_),
        )
        argv.append("-U" if unset_panes else "-u")
        if quiet:
            argv.append("-q")
        if option.target is not None:
            argv.extend(("-t", option.target))
        argv.append(option.name)
        return TmuxCommand(argv=tuple(argv))

    def show_option(
        self,
        *,
        option: TmuxOption,
        flags: TmuxShowFlags | None = None,
        format_: TmuxFormat | str | None = None,
    ) -> TmuxCommand:
        """Build show-options.

        Args:
            option: Option to show; an empty name lists every option.
            flags: Output modifiers.
            format_: Optional output format.

        Returns:
            Command.

        """
        show: Final[TmuxShowFlags] = flags or TmuxShowFlags()
        argv: list[str] = [self._executable, "show-options"]
        argv.extend(
            self._scope_flags(scope=option.scope, global_=option.global_),
        )
        if show.value_only:
            argv.append("-v")
        if show.quiet:
            argv.append("-q")
        if show.inherited:
            argv.append("-A")
        if show.hooks:
            argv.append("-H")
        if format_ is not None:
            argv.extend(("-F", TmuxFormat.raw(value=format_).text))
        if option.target is not None:
            argv.extend(("-t", option.target))
        if option.name:
            argv.append(option.name)
        return TmuxCommand(argv=tuple(argv))

    def set_user_option(
        self,
        *,
        name: str,
        value: str,
        scope: Scope = Scope.SESSION,
        target: str | None = None,
        global_: bool = False,
    ) -> TmuxCommand:
        """Set a tmux @user option.

        Args:
            name: User option with or without @.
            value: User-defined state.
            scope: Option scope.
            target: Optional target.
            global_: Whether option is global.

        Returns:
            Command.

        """
        normalized: str = name if name.startswith("@") else f"@{name}"
        return self.set_option(
            option=TmuxOption(
                name=normalized,
                value=value,
                scope=scope,
                target=target,
                global_=global_,
            ),
        )

    def set_status_format(
        self,
        *,
        index: int,
        format_: TmuxFormat | str,
        target: str | None = None,
        global_: bool = False,
    ) -> TmuxCommand:
        """Set one status-format array line.

        Args:
            index: Array index.
            format_: Complete line format.
            target: Optional session target.
            global_: Whether to set the global session option.

        Returns:
            Command.

        Raises:
            ValueError: If index is negative.

        """
        if index < 0:
            msg: str = "status-format index cannot be negative"
            raise ValueError(msg)
        return self.set_option(
            option=TmuxOption(
                name=f"status-format[{index}]",
                value=TmuxFormat.raw(value=format_),
                target=target,
                global_=global_,
            ),
        )

    def unset_status_format(
        self,
        *,
        index: int | None = None,
        target: str | None = None,
        global_: bool = False,
    ) -> TmuxCommand:
        """Unset one or all status-format lines.

        Args:
            index: Optional array index.
            target: Optional target.
            global_: Whether to reset the global option.

        Returns:
            Command.

        Raises:
            ValueError: If index is negative.

        """
        if index is not None and index < 0:
            msg: str = "status-format index cannot be negative"
            raise ValueError(msg)
        suffix: str = "" if index is None else f"[{index}]"
        return self.unset_option(
            option=TmuxOption(
                name=f"status-format{suffix}",
                value="",
                target=target,
                global_=global_,
            ),
        )

    def refresh_status(self, *, client: str | None = None) -> TmuxCommand:
        """Force status-line refresh.

        Args:
            client: Optional target client.

        Returns:
            refresh-client command.

        """
        argv: list[str] = [self._executable, "refresh-client", "-S"]
        if client is not None:
            argv.extend(("-t", client))
        return TmuxCommand(argv=tuple(argv))

    def display(
        self,
        *,
        format_: TmuxFormat | str,
        target: str | None = None,
    ) -> TmuxCommand:
        """Build display-message for evaluating a format.

        Args:
            format_: Format to evaluate.
            target: Optional target pane.

        Returns:
            display-message command.

        """
        argv: list[str] = [
            self._executable,
            "display-message",
            "-p",
            "-F",
            TmuxFormat.raw(value=format_).text,
        ]
        if target is not None:
            argv.extend(("-t", target))
        return TmuxCommand(argv=tuple(argv))

    def config_commands(
        self,
        *,
        config: TmuxStatusConfig,
        session_target: str | None = None,
        window_target: str | None = None,
        global_: bool = False,
    ) -> tuple[TmuxCommand, ...]:
        """Convert a typed configuration into set-option commands.

        Args:
            config: Configuration.
            session_target: Session target.
            window_target: Window target.
            global_: Apply as global options.

        Returns:
            Commands in deterministic dataclass order.

        """
        commands: list[TmuxCommand] = []
        for config_field in fields(config):
            value = getattr(config, config_field.name)
            if value is None:
                continue
            name, scope = self._STATUS_OPTIONS[config_field.name]
            target: str | None = (
                window_target if scope is Scope.WINDOW else session_target
            )
            commands.append(
                self.set_option(
                    option=TmuxOption(
                        name=name,
                        value=value,
                        scope=scope,
                        target=target,
                        global_=global_,
                    ),
                ),
            )
        return tuple(commands)

    def district_state_commands(
        self,
        *,
        target: str,
        state: TmuxDistrictState,
    ) -> tuple[TmuxCommand, ...]:
        """Build conventional District Terminal @user-option updates.

        Attached-client count is intentionally absent: tmux already
        exposes it natively as ``#{session_attached}``.

        Args:
            target: tmux session.
            state: District Terminal status values.

        Returns:
            User-option update commands.

        """
        values: Final[tuple[tuple[str, str | int | None], ...]] = (
            ("district_agent", state.agent),
            ("district_state", state.agent_state),
            ("district_id", state.id),
            ("district_initiator", state.initiator),
        )
        return tuple(
            self.set_user_option(
                name=name,
                value=str(value),
                target=target,
            )
            for name, value in values
            if value is not None
        )

    @staticmethod
    def argv(
        *,
        commands: tuple[TmuxCommand, ...],
    ) -> tuple[tuple[str, ...], ...]:
        """Extract argv tuples.

        Args:
            commands: Commands.

        Returns:
            argv sequences.

        """
        return tuple(command.argv for command in commands)

    @staticmethod
    def shell_commands(
        *,
        commands: tuple[TmuxCommand, ...],
    ) -> tuple[str, ...]:
        """Render commands for humans.

        Args:
            commands: Commands.

        Returns:
            Shell-escaped commands.

        """
        return tuple(command.shell() for command in commands)


if __name__ == "__main__":
    manager: Final[TmuxStatusManager] = TmuxStatusManager()
    district_left: Final[TmuxFormat] = (
        TmuxFormat.literal(value=" DISTRICT │ ")
        .then(other=TmuxFormat.user_option(name="district_id"))
        .then(other=" │ ")
        .then(other=TmuxFormat.user_option(name="district_agent"))
        .then(other=" ")
    )
    district_right: Final[TmuxFormat] = (
        TmuxFormat.user_option(name="district_state")
        .then(other=" │ attached: ")
        .then(other=Formats.SESSION_ATTACHED)
        .then(other=" │ ")
        .then(other=Formats.PANE_COMMAND)
        .then(other=" │ %H:%M ")
    )
    config: Final[TmuxStatusConfig] = TmuxStatusConfig(
        rows=True,
        position=StatusPosition.BOTTOM,
        interval_seconds=1,
        left=district_left,
        left_length=100,
        right=district_right,
        right_length=160,
        window_format=" #I:#W ",
        window_current_format="#[bold] #I:#W #[default]",
        window_separator="│",
    )
    commands: Final[tuple[TmuxCommand, ...]] = (
        *manager.config_commands(
            config=config,
            session_target="district-main",
            window_target="district-main:",
        ),
        *manager.district_state_commands(
            target="district-main",
            state=TmuxDistrictState(
                agent="devin",
                agent_state="EXECUTING",
                id="abc123",
                initiator="joaolopes",
            ),
        ),
        manager.refresh_status(),
    )
    rendered: Final[str] = "\n".join(
        manager.shell_commands(commands=commands),
    )
    stdout.write(f"{rendered}\n")
