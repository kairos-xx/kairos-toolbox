# Copyright (c) 2026 Project contributors.
"""Typed tmux status-line configuration, format DSL, and CLI renderer."""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum
from shlex import join
from sys import stdout
from typing import Final, Self, final


@final
class Constants:
    """Group module-level constants.

    Attributes:
        max_colour_index: Grouped module constant.
        rgb_hex_length: Grouped module constant.
        control_range_max: Grouped module constant.
        user_range_max_bytes: Grouped module constant.

    """

    max_colour_index: Final[int] = 255
    rgb_hex_length: Final[int] = 7
    control_range_max: Final[int] = 9
    user_range_max_bytes: Final[int] = 15


class Scope(StrEnum):
    """tmux option scopes.

    Attributes:
    pane: Pane.
    server: Server.
    session: Session.
    window: Window.

    """

    session = "session"
    window = "window"
    pane = "pane"
    server = "server"


class StatusPosition(StrEnum):
    """Status-line positions.

    Attributes:
    bottom: Bottom.
    top: Top.

    """

    top = "top"
    bottom = "bottom"


class StatusJustify(StrEnum):
    """Window-list justification.

    Attributes:
    absolute_centre: Absolute centre.
    centre: Centre.
    left: Left.
    right: Right.

    """

    left = "left"
    centre = "centre"
    right = "right"
    absolute_centre = "absolute-centre"


class StatusKeys(StrEnum):
    """Status prompt key modes.

    Attributes:
    emacs: Emacs.
    vi: Vi.

    """

    emacs = "emacs"
    vi = "vi"


class ColourName(StrEnum):
    """Named tmux colours.

    Attributes:
    black: Black.
    blue: Blue.
    brightblack: Brightblack.
    brightblue: Brightblue.
    brightcyan: Brightcyan.
    brightgreen: Brightgreen.
    brightmagenta: Brightmagenta.
    brightred: Brightred.
    brightwhite: Brightwhite.
    brightyellow: Brightyellow.
    cyan: Cyan.
    default: Default.
    green: Green.
    magenta: Magenta.
    red: Red.
    terminal: Terminal.
    white: White.
    yellow: Yellow.

    """

    default = "default"
    terminal = "terminal"
    black = "black"
    red = "red"
    green = "green"
    yellow = "yellow"
    blue = "blue"
    magenta = "magenta"
    cyan = "cyan"
    white = "white"
    brightblack = "brightblack"
    brightred = "brightred"
    brightgreen = "brightgreen"
    brightyellow = "brightyellow"
    brightblue = "brightblue"
    brightmagenta = "brightmagenta"
    brightcyan = "brightcyan"
    brightwhite = "brightwhite"


class Align(StrEnum):
    """Inline style alignment.

    Attributes:
    centre: Centre.
    left: Left.
    right: Right.

    """

    left = "left"
    centre = "centre"
    right = "right"


class ListMarker(StrEnum):
    """status-format window-list markers.

    Attributes:
    focus: Focus.
    left_marker: Left marker.
    on: On.
    right_marker: Right marker.

    """

    on = "on"
    focus = "focus"
    left_marker = "left-marker"
    right_marker = "right-marker"


class RangeKind(StrEnum):
    """Mouse-sensitive status ranges.

    Attributes:
    control: Control.
    left: Left.
    pane: Pane.
    right: Right.
    session: Session.
    user: User.
    window: Window.

    """

    left = "left"
    right = "right"
    session = "session"
    window = "window"
    pane = "pane"
    user = "user"
    control = "control"


@dataclass(frozen=True, slots=True, kw_only=True)
class Colour:
    """Represent a tmux colour.

    Attributes:
    value: Value.

    """

    value: str

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
        if not 0 <= index <= Constants.max_colour_index:
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
        if any(
            not 0 <= channel <= Constants.max_colour_index
            for channel in channels
        ):
            msg: str = "RGB channels must be between 0 and 255"
            raise ValueError(msg)
        return cls(value=f"#{red:02x}{green:02x}{blue:02x}")

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
            and (0 <= int(self.value[6:]) <= Constants.max_colour_index)
        )
        valid_rgb: bool = (
            len(self.value) == Constants.rgb_hex_length
            and self.value.startswith("#")
            and all(
                char in "0123456789abcdefABCDEF" for char in self.value[1:]
            )
        )
        if self.value not in named and (not valid_index) and (not valid_rgb):
            msg: str = f"invalid tmux colour: {self.value}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class TmuxRange:
    """Represent a tmux inline mouse range.

    Attributes:
    kind: Kind.
    value: Value.

    """

    kind: RangeKind
    value: str | int | None = None

    def render(self) -> str:
        """Render the range.

        Returns:
            tmux range style fragment.

        Raises:
            ValueError: If the range value is invalid for its kind.

        """
        if self.kind in {RangeKind.left, RangeKind.right}:
            if self.value is not None:
                msg: str = "left/right ranges do not accept values"
                raise ValueError(msg)
            return f"range={self.kind.value}"
        if self.value is None:
            msg = f"{self.kind.value} range requires a value"
            raise ValueError(msg)
        if self.kind is RangeKind.control:
            if (
                not isinstance(self.value, int)
                or not 0 <= self.value <= Constants.control_range_max
            ):
                msg = "control range must be an integer from 0 to 9"
                raise ValueError(msg)
        elif isinstance(self.value, int):
            msg = "only control ranges accept integer values"
            raise ValueError(msg)
        elif (
            self.kind is RangeKind.user
            and len(self.value.encode()) > Constants.user_range_max_bytes
        ):
            msg = "user range value must be at most 15 bytes"
            raise ValueError(msg)
        return f"range={self.kind.value}|{self.value}"


@dataclass(frozen=True, slots=True, kw_only=True)
class TmuxStyle:
    """Build a tmux style without hand-written style strings.

    Attributes:
    align: Align.
    background: Background.
    blink: Blink.
    bold: Bold.
    dim: Dim.
    fill: Fill.
    foreground: Foreground.
    hidden: Hidden.
    italics: Italics.
    link: Link.
    list_marker: List marker.
    noattr: Noattr.
    pop_default: Pop default.
    push_default: Push default.
    range_: Range.
    reverse: Reverse.
    set_default: Set default.
    strikethrough: Strikethrough.
    underscore: Underscore.
    width: Width.

    """

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


@dataclass(frozen=True, slots=True, kw_only=True)
class TmuxFormat:
    """Composable tmux format expression.

    Attributes:
    text: Text.

    """

    text: str

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
        return cls(text=f"#{{?{condition_text},{true_text},{false_text}}}")

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

    @classmethod
    def raw(cls, *, value: TmuxFormat | str) -> TmuxFormat:
        """Normalize format input.

        Args:
            value: Existing format or raw tmux text.

        Returns:
            Format instance.

        """
        if isinstance(value, TmuxFormat):
            return value
        return cls(text=value)

    def then(self, *, other: TmuxFormat | str) -> TmuxFormat:
        """Concatenate another format.

        Args:
            other: Format or literal text.

        Returns:
            Combined format.

        """
        return TmuxFormat(text=self.text + TmuxFormat.raw(value=other).text)

    def __str__(self) -> str:
        """Return format text.

        Returns:
            Raw tmux format.

        """
        return self.text


@final
class Formats:
    """Common tmux status variables and aliases.

    Attributes:
    client_height: Client height.
    client_name: Client name.
    client_termname: Client termname.
    client_width: Client width.
    host: Host.
    host_short: Host short.
    pane_active: Pane active.
    pane_command: Pane command.
    pane_dead: Pane dead.
    pane_dead_status: Pane dead status.
    pane_height: Pane height.
    pane_id: Pane id.
    pane_index: Pane index.
    pane_path: Pane path.
    pane_pid: Pane pid.
    pane_width: Pane width.
    session_attached: Session attached.
    session_id: Session id.
    session_name: Session name.
    session_windows: Session windows.
    socket_path: Socket path.
    version: Version.
    window_active: Window active.
    window_activity: Window activity.
    window_bell: Window bell.
    window_flags: Window flags.
    window_id: Window id.
    window_index: Window index.
    window_name: Window name.

    """

    session_name: Final[TmuxFormat] = TmuxFormat.variable(name="session_name")
    session_id: Final[TmuxFormat] = TmuxFormat.variable(name="session_id")
    session_windows: Final[TmuxFormat] = TmuxFormat.variable(
        name="session_windows",
    )
    session_attached: Final[TmuxFormat] = TmuxFormat.variable(
        name="session_attached",
    )
    window_index: Final[TmuxFormat] = TmuxFormat.variable(name="window_index")
    window_name: Final[TmuxFormat] = TmuxFormat.variable(name="window_name")
    window_id: Final[TmuxFormat] = TmuxFormat.variable(name="window_id")
    window_flags: Final[TmuxFormat] = TmuxFormat.variable(name="window_flags")
    window_active: Final[TmuxFormat] = TmuxFormat.variable(
        name="window_active",
    )
    window_activity: Final[TmuxFormat] = TmuxFormat.variable(
        name="window_activity",
    )
    window_bell: Final[TmuxFormat] = TmuxFormat.variable(
        name="window_bell_flag",
    )
    pane_index: Final[TmuxFormat] = TmuxFormat.variable(name="pane_index")
    pane_id: Final[TmuxFormat] = TmuxFormat.variable(name="pane_id")
    pane_pid: Final[TmuxFormat] = TmuxFormat.variable(name="pane_pid")
    pane_command: Final[TmuxFormat] = TmuxFormat.variable(
        name="pane_current_command",
    )
    pane_path: Final[TmuxFormat] = TmuxFormat.variable(
        name="pane_current_path",
    )
    pane_active: Final[TmuxFormat] = TmuxFormat.variable(name="pane_active")
    pane_dead: Final[TmuxFormat] = TmuxFormat.variable(name="pane_dead")
    pane_dead_status: Final[TmuxFormat] = TmuxFormat.variable(
        name="pane_dead_status",
    )
    pane_width: Final[TmuxFormat] = TmuxFormat.variable(name="pane_width")
    pane_height: Final[TmuxFormat] = TmuxFormat.variable(name="pane_height")
    client_name: Final[TmuxFormat] = TmuxFormat.variable(name="client_name")
    client_termname: Final[TmuxFormat] = TmuxFormat.variable(
        name="client_termname",
    )
    client_width: Final[TmuxFormat] = TmuxFormat.variable(name="client_width")
    client_height: Final[TmuxFormat] = TmuxFormat.variable(
        name="client_height",
    )
    host: Final[TmuxFormat] = TmuxFormat.variable(name="host")
    host_short: Final[TmuxFormat] = TmuxFormat.variable(name="host_short")
    version: Final[TmuxFormat] = TmuxFormat.variable(name="version")
    socket_path: Final[TmuxFormat] = TmuxFormat.variable(name="socket_path")


@dataclass(frozen=True, slots=True, kw_only=True)
class TmuxStatusConfig:
    """Describe status and window-status options.

    Attributes:
    interval_seconds: Interval seconds.
    justify: Justify.
    keys: Keys.
    left: Left.
    left_length: Left length.
    left_style: Left style.
    message_command_style: Message command style.
    message_style: Message style.
    pane_current_status_format: Pane current status format.
    pane_status_format: Pane status format.
    position: Position.
    right: Right.
    right_length: Right length.
    right_style: Right style.
    rows: Rows.
    style: Style.
    window_activity_style: Window activity style.
    window_bell_style: Window bell style.
    window_current_format: Window current format.
    window_current_style: Window current style.
    window_format: Window format.
    window_last_style: Window last style.
    window_separator: Window separator.
    window_silence_style: Window silence style.
    window_style: Window style.

    """

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
            and (not isinstance(self.rows, bool))
            and (self.rows not in {2, 3, 4, 5})
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
    """One safe argv-based tmux command.

    Attributes:
    argv: Argv.

    """

    argv: tuple[str, ...]

    def shell(self) -> str:
        """Render for display.

        Returns:
            Shell-escaped command.

        """
        return join(split_command=self.argv)


type TmuxValue = bool | int | str | StrEnum | TmuxFormat | TmuxStyle


@dataclass(frozen=True, slots=True, kw_only=True)
class TmuxOption:
    """Generic option assignment.

    Attributes:
    global_: Global.
    name: Name.
    scope: Scope.
    target: Target.
    value: Value.

    """

    name: str
    value: TmuxValue
    scope: Scope = Scope.session
    target: str | None = None
    global_: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class TmuxShowFlags:
    """Display modifiers for show-options.

    Attributes:
    hooks: Hooks.
    inherited: Inherited.
    quiet: Quiet.
    value_only: Value only.

    """

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


@final
class TmuxStatusManager:
    """Build all tmux commands needed to manage status presentation."""

    _status_options: Final[dict[str, tuple[str, Scope]]] = {
        "rows": ("status", Scope.session),
        "position": ("status-position", Scope.session),
        "interval_seconds": ("status-interval", Scope.session),
        "justify": ("status-justify", Scope.session),
        "keys": ("status-keys", Scope.session),
        "left": ("status-left", Scope.session),
        "left_length": ("status-left-length", Scope.session),
        "left_style": ("status-left-style", Scope.session),
        "right": ("status-right", Scope.session),
        "right_length": ("status-right-length", Scope.session),
        "right_style": ("status-right-style", Scope.session),
        "style": ("status-style", Scope.session),
        "window_format": ("window-status-format", Scope.window),
        "window_current_format": (
            "window-status-current-format",
            Scope.window,
        ),
        "window_style": ("window-status-style", Scope.window),
        "window_current_style": ("window-status-current-style", Scope.window),
        "window_last_style": ("window-status-last-style", Scope.window),
        "window_activity_style": (
            "window-status-activity-style",
            Scope.window,
        ),
        "window_bell_style": ("window-status-bell-style", Scope.window),
        "window_silence_style": ("window-status-silence-style", Scope.window),
        "window_separator": ("window-status-separator", Scope.window),
        "pane_status_format": ("window-pane-status-format", Scope.window),
        "pane_current_status_format": (
            "window-pane-current-status-format",
            Scope.window,
        ),
        "message_style": ("message-style", Scope.session),
        "message_command_style": ("message-command-style", Scope.session),
    }

    def __init__(self, *, executable: str = "tmux") -> None:
        """Initialize the manager.

        Args:
            executable: tmux executable or absolute path.

        """
        self._executable: Final[str] = executable

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
        scope: Scope = Scope.session,
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
        for config_field in fields(class_or_instance=config):
            value: TmuxValue | None = getattr(config, config_field.name)
            if value is None:
                continue
            name, scope = self._status_options[config_field.name]
            target: str | None = (
                window_target if scope is Scope.window else session_target
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
            (
                self.set_user_option(
                    name=name,
                    value=str(object=value),
                    target=target,
                )
                for name, value in values
                if value is not None
            ),
        )

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
        return str(object=value)

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
            Scope.session: "",
            Scope.window: "-w",
            Scope.pane: "-p",
            Scope.server: "-s",
        }
        flags: list[str] = []
        if scope_flag[scope]:
            flags.append(scope_flag[scope])
        if global_:
            flags.append("-g")
        return tuple(flags)


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
        .then(other=Formats.session_attached)
        .then(other=" │ ")
        .then(other=Formats.pane_command)
        .then(other=" │ %H:%M ")
    )
    config: Final[TmuxStatusConfig] = TmuxStatusConfig(
        rows=True,
        position=StatusPosition.bottom,
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
    rendered: Final[str] = "\n".join(manager.shell_commands(commands=commands))
    stdout.write(f"{rendered}\n")
