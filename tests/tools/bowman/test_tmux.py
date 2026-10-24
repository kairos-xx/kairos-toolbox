# Copyright (c) 2026 Project contributors.
"""Bowman tmux status DSL and command builder tests."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess, run
from sys import executable
from typing import Final

import bowman
import pytest
from bowman.tmux import (
    Align,
    Colour,
    ColourName,
    Formats,
    ListMarker,
    RangeKind,
    Scope,
    StatusJustify,
    StatusKeys,
    StatusPosition,
    TmuxCommand,
    TmuxFormat,
    TmuxLakeState,
    TmuxOption,
    TmuxRange,
    TmuxShowFlags,
    TmuxStatusConfig,
    TmuxStatusManager,
    TmuxStyle,
)

from .helpers import check, expect

MANAGER: Final[TmuxStatusManager] = TmuxStatusManager()


def test_colour_accepts_named_values() -> None:
    """Named tmux colours validate."""
    expect(actual=Colour(value="red").value, expected="red")
    expect(
        actual=Colour(value=ColourName.BRIGHTCYAN.value).value,
        expected="brightcyan",
    )


def test_colour_indexed_validates_range() -> None:
    """Indexed colours enforce the 0-255 palette."""
    expect(actual=Colour.indexed(index=0).value, expected="colour0")
    expect(actual=Colour.indexed(index=255).value, expected="colour255")
    with pytest.raises(
        expected_exception=ValueError, match="between 0 and 255",
    ):
        Colour.indexed(index=256)
    with pytest.raises(
        expected_exception=ValueError, match="invalid tmux colour",
    ):
        Colour(value="colour256")


def test_colour_rgb_validates_channels() -> None:
    """RGB colours enforce channels and hex shape."""
    expect(
        actual=Colour.rgb(red=255, green=0, blue=16).value,
        expected="#ff0010",
    )
    with pytest.raises(
        expected_exception=ValueError, match="RGB channels",
    ):
        Colour.rgb(red=256, green=0, blue=0)
    with pytest.raises(
        expected_exception=ValueError, match="invalid tmux colour",
    ):
        Colour(value="#xyz")
    with pytest.raises(
        expected_exception=ValueError, match="invalid tmux colour",
    ):
        Colour(value="not-a-colour")


def test_range_renders_marker_kinds() -> None:
    """Left/right ranges render bare and reject values."""
    expect(
        actual=TmuxRange(kind=RangeKind.LEFT).render(),
        expected="range=left",
    )
    with pytest.raises(
        expected_exception=ValueError, match="do not accept values",
    ):
        TmuxRange(kind=RangeKind.RIGHT, value="x").render()


def test_range_renders_value_kinds() -> None:
    """Value ranges render ``kind|value`` and require a value."""
    expect(
        actual=TmuxRange(kind=RangeKind.PANE, value="%3").render(),
        expected="range=pane|%3",
    )
    expect(
        actual=TmuxRange(kind=RangeKind.USER, value="tag").render(),
        expected="range=user|tag",
    )
    with pytest.raises(
        expected_exception=ValueError, match="requires a value",
    ):
        TmuxRange(kind=RangeKind.SESSION).render()


def test_range_validates_control_and_user() -> None:
    """Control ranges need ints 0-9; user ranges cap at 15 bytes."""
    expect(
        actual=TmuxRange(kind=RangeKind.CONTROL, value=4).render(),
        expected="range=control|4",
    )
    with pytest.raises(
        expected_exception=ValueError, match="integer from 0 to 9",
    ):
        TmuxRange(kind=RangeKind.CONTROL, value=10).render()
    with pytest.raises(
        expected_exception=ValueError, match="integer from 0 to 9",
    ):
        TmuxRange(kind=RangeKind.CONTROL, value="x").render()
    with pytest.raises(
        expected_exception=ValueError, match="only control ranges",
    ):
        TmuxRange(kind=RangeKind.WINDOW, value=1).render()
    with pytest.raises(
        expected_exception=ValueError, match="at most 15 bytes",
    ):
        TmuxRange(kind=RangeKind.USER, value="x" * 16).render()


def test_style_render_empty_and_typed_fields() -> None:
    """Styles render defaults, colours, layout, and markers."""
    expect(actual=TmuxStyle().render(), expected="default")
    style: TmuxStyle = TmuxStyle(
        foreground=Colour.indexed(index=250),
        background=Colour(value="black"),
        fill=Colour.rgb(red=1, green=2, blue=3),
        width=10,
        align=Align.CENTRE,
        range_=TmuxRange(kind=RangeKind.PANE, value="%1"),
        list_marker=ListMarker.FOCUS,
    )
    expect(
        actual=style.render(),
        expected=(
            "fg=colour250,bg=black,fill=#010203,width=10,align=centre,"
            "range=pane|%1,list=focus"
        ),
    )


def test_style_render_link_and_attributes() -> None:
    """Link and attribute flags render with no-prefixed negations."""
    expect(
        actual=TmuxStyle(link="x").render(), expected="link=x",
    )
    expect(actual=TmuxStyle(link="").render(), expected="nolink")
    style: TmuxStyle = TmuxStyle(
        bold=True,
        dim=False,
        italics=True,
        noattr=True,
        push_default=True,
        pop_default=True,
        set_default=True,
    )
    expect(
        actual=style.render(),
        expected=(
            "bold,nodim,italics,noattr,"
            "push-default,pop-default,set-default"
        ),
    )


def test_format_composition_and_escaping() -> None:
    """Formats chain, escape literals, and normalize inputs."""
    expect(
        actual=TmuxFormat.literal(value="a#b").text,
        expected="a##b",
    )
    expect(
        actual=TmuxFormat.variable(name="pane_id").text,
        expected="#{pane_id}",
    )
    expect(
        actual=TmuxFormat.user_option(name="lake_id").text,
        expected="#{@lake_id}",
    )
    expect(
        actual=TmuxFormat.user_option(name="@lake_id").text,
        expected="#{@lake_id}",
    )
    expect(
        actual=str(
            TmuxFormat.literal(value="x").then(other=Formats.PANE_ID),
        ),
        expected="x#{pane_id}",
    )
    expect(
        actual=TmuxFormat.raw(value="#{a}").text,
        expected="#{a}",
    )


def test_format_conditional_operator_match() -> None:
    """Conditionals unwrap variables; operators and matches render."""
    expect(
        actual=TmuxFormat.conditional(
            condition="pane_in_mode",
            when_true="copy",
            when_false="run",
        ).text,
        expected="#{?pane_in_mode,copy,run}",
    )
    expect(
        actual=TmuxFormat.conditional(
            condition=Formats.PANE_DEAD,
            when_true="D",
            when_false="",
        ).text,
        expected="#{?pane_dead,D,}",
    )
    expect(
        actual=TmuxFormat.operator(
            operator="==",
            left=Formats.PANE_COMMAND,
            right="ssh",
        ).text,
        expected="#{==:#{pane_current_command},ssh}",
    )
    expect(
        actual=TmuxFormat.match(pattern="dev*", value=Formats.HOST).text,
        expected="#{m:dev*,#{host}}",
    )
    expect(
        actual=TmuxFormat.match(
            pattern="dev",
            value="x",
            regex=True,
            ignore_case=True,
        ).text,
        expected="#{m/ri:dev,x}",
    )


def test_format_modified_time_loop_shell_styled() -> None:
    """Modifier, time, loop, shell, and styled formats render."""
    expect(
        actual=TmuxFormat.modified(
            modifiers="b", value="pane_current_path",
        ).text,
        expected="#{b:pane_current_path}",
    )
    expect(
        actual=TmuxFormat.time(variable="activity").text,
        expected="#{t:activity}",
    )
    expect(
        actual=TmuxFormat.time(
            variable="a",
            relative=True,
            difference=True,
            pretty=True,
            format_="%H",
        ).text,
        expected="#{t/p/r/d/f/%H:a}",
    )
    expect(
        actual=TmuxFormat.loop(
            kind="W",
            format_="#{window_name}",
            current_format="#{window_active}",
            flags="a",
        ).text,
        expected="#{W/a:#{window_name},#{window_active}}",
    )
    expect(
        actual=TmuxFormat.shell_command(command="uptime").text,
        expected="#(uptime)",
    )
    expect(
        actual=TmuxFormat.styled(
            value="x",
            style=TmuxStyle(bold=True),
        ).text,
        expected="#[bold]x#[default]",
    )
    expect(
        actual=TmuxFormat.styled(
            value="x",
            style=TmuxStyle(bold=True),
            reset=False,
        ).text,
        expected="#[bold]x",
    )


def test_status_config_validates_rows_and_numbers() -> None:
    """Row counts and negative numerics are rejected."""
    expect(actual=TmuxStatusConfig(rows=True).rows, expected=True)
    expect(actual=TmuxStatusConfig(rows=3).rows, expected=3)
    with pytest.raises(
        expected_exception=ValueError, match="status rows",
    ):
        TmuxStatusConfig(rows=6)
    with pytest.raises(
        expected_exception=ValueError, match="cannot be negative",
    ):
        TmuxStatusConfig(interval_seconds=-1)
    with pytest.raises(
        expected_exception=ValueError, match="cannot be negative",
    ):
        TmuxStatusConfig(right_length=-2)


def test_set_option_builds_scoped_argv() -> None:
    """set-option renders scopes, flags, targets, and typed values."""
    command: TmuxCommand = MANAGER.set_option(
        option=TmuxOption(
            name="status",
            value=True,
            scope=Scope.WINDOW,
            target="s:",
            global_=True,
        ),
        append=True,
        only_if_unset=True,
        quiet=True,
        expand=True,
    )
    expect(
        actual=command.argv,
        expected=(
            "tmux",
            "set-option",
            "-w",
            "-g",
            "-a",
            "-o",
            "-q",
            "-F",
            "-t",
            "s:",
            "status",
            "on",
        ),
    )


def test_set_option_renders_typed_values() -> None:
    """Option values render enums, formats, styles, and numbers."""
    command: TmuxCommand = MANAGER.set_option(
        option=TmuxOption(
            name="status-position",
            value=StatusPosition.TOP,
        ),
    )
    expect(actual=command.argv[-1], expected="top")
    command = MANAGER.set_option(
        option=TmuxOption(name="status-left", value=Formats.HOST_SHORT),
    )
    expect(actual=command.argv[-1], expected="#{host_short}")
    command = MANAGER.set_option(
        option=TmuxOption(name="status-style", value=TmuxStyle(bold=True)),
    )
    expect(actual=command.argv[-1], expected="bold")
    command = MANAGER.set_option(
        option=TmuxOption(name="status-interval", value=5),
    )
    expect(actual=command.argv[-1], expected="5")


def test_unset_and_show_option_build_argv() -> None:
    """Option reset and show commands render their switches."""
    expect(
        actual=MANAGER.unset_option(
            option=TmuxOption(
                name="status",
                value="",
                scope=Scope.PANE,
                target="%1",
            ),
            quiet=True,
            unset_panes=True,
        ).argv,
        expected=(
            "tmux", "set-option", "-p", "-U", "-q", "-t", "%1", "status",
        ),
    )
    expect(
        actual=MANAGER.unset_option(
            option=TmuxOption(name="status", value=""),
        ).argv,
        expected=("tmux", "set-option", "-u", "status"),
    )
    expect(
        actual=MANAGER.show_option(
            option=TmuxOption(
                name="status",
                value="",
                scope=Scope.SERVER,
            ),
            flags=TmuxShowFlags(
                value_only=True,
                quiet=True,
                inherited=True,
                hooks=True,
            ),
            format_="#{option_name}",
        ).argv,
        expected=(
            "tmux",
            "show-options",
            "-s",
            "-v",
            "-q",
            "-A",
            "-H",
            "-F",
            "#{option_name}",
            "status",
        ),
    )


def test_show_option_omits_empty_name_and_defaults() -> None:
    """An empty option name lists every option."""
    expect(
        actual=MANAGER.show_option(
            option=TmuxOption(name="", value=""),
        ).argv,
        expected=("tmux", "show-options"),
    )


def test_user_option_normalizes_prefix() -> None:
    """User options gain the @ prefix when missing."""
    expect(
        actual=MANAGER.set_user_option(
            name="lake_state", value="ready", target="s",
        ).argv,
        expected=(
            "tmux", "set-option", "-t", "s", "@lake_state", "ready",
        ),
    )
    expect(
        actual=MANAGER.set_user_option(
            name="@x", value="1", global_=True,
        ).argv,
        expected=("tmux", "set-option", "-g", "@x", "1"),
    )


def test_status_format_array_commands() -> None:
    """status-format array indices are set and unset safely."""
    expect(
        actual=MANAGER.set_status_format(
            index=1, format_="#[list=on]x",
        ).argv,
        expected=(
            "tmux", "set-option", "status-format[1]", "#[list=on]x",
        ),
    )
    expect(
        actual=MANAGER.unset_status_format(index=2).argv,
        expected=("tmux", "set-option", "-u", "status-format[2]"),
    )
    expect(
        actual=MANAGER.unset_status_format().argv,
        expected=("tmux", "set-option", "-u", "status-format"),
    )
    with pytest.raises(
        expected_exception=ValueError, match="cannot be negative",
    ):
        MANAGER.set_status_format(index=-1, format_="x")
    with pytest.raises(
        expected_exception=ValueError, match="cannot be negative",
    ):
        MANAGER.unset_status_format(index=-1)


def test_refresh_and_display_commands() -> None:
    """Refresh and display-message commands render targets."""
    expect(
        actual=MANAGER.refresh_status().argv,
        expected=("tmux", "refresh-client", "-S"),
    )
    expect(
        actual=MANAGER.refresh_status(client="c1").argv,
        expected=("tmux", "refresh-client", "-S", "-t", "c1"),
    )
    expect(
        actual=MANAGER.display(
            format_=Formats.VERSION, target="%0",
        ).argv,
        expected=(
            "tmux",
            "display-message",
            "-p",
            "-F",
            "#{version}",
            "-t",
            "%0",
        ),
    )


def test_config_commands_route_targets_by_scope() -> None:
    """Config fields map to options with per-scope targets."""
    commands: tuple[TmuxCommand, ...] = MANAGER.config_commands(
        config=TmuxStatusConfig(
            rows=True,
            position=StatusPosition.BOTTOM,
            interval_seconds=1,
            justify=StatusJustify.ABSOLUTE_CENTRE,
            keys=StatusKeys.VI,
            left="L",
            window_format="W",
            message_style=TmuxStyle(dim=True),
        ),
        session_target="lake-main",
        window_target="lake-main:",
        global_=True,
    )
    rendered: tuple[str, ...] = MANAGER.shell_commands(commands=commands)
    expect(actual=len(commands), expected=8)
    expect(
        actual=rendered[0],
        expected="tmux set-option -g -t lake-main status on",
    )
    expect(
        actual=rendered[1],
        expected="tmux set-option -g -t lake-main status-position bottom",
    )
    expect(
        actual=rendered[5],
        expected="tmux set-option -g -t lake-main status-left L",
    )
    expect(
        actual=rendered[6],
        expected=(
            "tmux set-option -w -g -t lake-main:"
            " window-status-format W"
        ),
    )
    expect(
        actual=rendered[7],
        expected=(
            "tmux set-option -g -t lake-main message-style dim"
        ),
    )
    expect(
        actual=MANAGER.argv(commands=commands)[-1],
        expected=(
            "tmux",
            "set-option",
            "-g",
            "-t",
            "lake-main",
            "message-style",
            "dim",
        ),
    )
    check(
        condition=MANAGER.config_commands(
            config=TmuxStatusConfig(),
        )
        == (),
        reason="empty config emits no commands",
    )


def test_lake_state_commands_render_user_options() -> None:
    """Lake state maps present values to @-option updates."""
    commands: tuple[TmuxCommand, ...] = MANAGER.lake_state_commands(
        target="lake-main",
        state=TmuxLakeState(
            agent="Devin",
            state="ready",
            clients=2,
            identity="abc123",
        ),
    )
    expect(
        actual=tuple(command.argv for command in commands),
        expected=(
            (
                "tmux", "set-option", "-t", "lake-main",
                "@lake_agent", "Devin",
            ),
            (
                "tmux", "set-option", "-t", "lake-main",
                "@lake_state", "ready",
            ),
            (
                "tmux", "set-option", "-t", "lake-main",
                "@lake_clients", "2",
            ),
            (
                "tmux", "set-option", "-t", "lake-main",
                "@lake_id", "abc123",
            ),
        ),
    )
    check(
        condition=MANAGER.lake_state_commands(
            target="s", state=TmuxLakeState(),
        )
        == (),
        reason="empty lake state emits no commands",
    )


def test_executable_override_and_command_shell() -> None:
    """A custom executable is honoured and argv shells escape."""
    manager: TmuxStatusManager = TmuxStatusManager(
        executable="/opt/tmux/bin/tmux",
    )
    command: TmuxCommand = manager.set_option(
        option=TmuxOption(name="x", value="a b"),
    )
    expect(actual=command.argv[0], expected="/opt/tmux/bin/tmux")
    expect(
        actual=command.shell(),
        expected="/opt/tmux/bin/tmux set-option x 'a b'",
    )


def test_main_block_renders_demo_commands() -> None:
    """The ``python -m bowman.tmux`` demo prints generated commands."""
    tools_dir: Path = Path(bowman.__file__).resolve().parent.parent
    result: CompletedProcess[str] = run(
        args=[executable, "-m", "bowman.tmux"],
        cwd=tools_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    output: str = result.stdout
    check(
        condition="tmux set-option -t lake-main status on" in output,
        reason="demo must render status option commands",
    )
    check(
        condition="@lake_state ready" in output,
        reason="demo must render lake user-option commands",
    )
    check(
        condition="tmux refresh-client -S" in output,
        reason="demo must render a refresh command",
    )
