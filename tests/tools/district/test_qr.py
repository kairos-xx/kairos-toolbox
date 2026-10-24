# Copyright (c) 2026 Project contributors.
"""Tests for the terminal QR code and the banner that carries the link."""

from __future__ import annotations

from math import ceil
from typing import TYPE_CHECKING, Final, final

from cv2 import BORDER_CONSTANT, QRCodeDetector, copyMakeBorder
from district.qr import (
    BitBuffer,
    ErrorCorrection,
    Galois,
    PairingBanner,
    QrCode,
    QrConstants,
    QrRenderer,
)
from numpy import (
    asarray,
    dtype,
    ndarray,
    pad,
    repeat,
    uint8,
    where,
)

from .helpers import check, expect

if TYPE_CHECKING:
    from cv2.typing import MatLike


@final
class Constants:
    """Group test constants.

    Attributes:
        url: A pairing link as the server prints it.
        headline: Instruction lines shown above the link.
        finder: Side of the square finder pattern in every QR code.
        light_ring: Distance from the centre of the finder's light ring.
        centre: Index of the finder's middle module.
        plain_glyphs: The only characters a plain drawing may contain.

    """

    url: Final[str] = (
        "http://192.168.1.20:8765/pair?t=n.mq1x2y.AbCdEfGhIjKl."
        "0123456789abcdefghijkl"
    )
    headline: Final[tuple[str, ...]] = (
        "District Terminal remote console",
        "Scan with your phone's camera.",
    )
    finder: Final[int] = 7
    light_ring: Final[int] = 2
    centre: Final[int] = 3
    plain_glyphs: Final[frozenset[str]] = frozenset(
        {"█", "▄", "▀", " "},
    )


def test_the_symbol_is_square_with_a_quiet_zone() -> None:
    """Every row has the same width and the border is entirely light."""
    rows: tuple[tuple[bool, ...], ...] = QrRenderer.modules(text=Constants.url)
    width: int = len(rows[0])
    expect(actual=len(rows), expected=width)
    border: int = QrConstants.border
    for row in rows[:border] + rows[-border:]:
        check(
            condition=not any(row),
            reason="top and bottom margins are light",
        )
    for row in rows:
        expect(actual=len(row), expected=width)
        check(
            condition=not any(row[:border]) and not any(row[-border:]),
            reason="left and right margins are light",
        )


def test_the_three_finder_patterns_are_where_scanners_look() -> None:
    """Top-left, top-right and bottom-left corners hold a 7x7 finder.

    A finder is a dark ring, a light ring and a dark 3x3 centre.
    """
    rows: tuple[tuple[bool, ...], ...] = QrRenderer.modules(text=Constants.url)
    border: int = QrConstants.border
    far: int = len(rows) - border - Constants.finder
    for top, left in ((border, border), (border, far), (far, border)):
        for dy in range(Constants.finder):
            for dx in range(Constants.finder):
                ring: int = max(
                    abs(dy - Constants.centre),
                    abs(dx - Constants.centre),
                )
                expected: bool = ring != Constants.light_ring
                expect(actual=rows[top + dy][left + dx], expected=expected)


def test_different_links_make_different_symbols() -> None:
    """The symbol really encodes its text."""
    other: str = Constants.url.replace("pair?t=n", "pair?t=m")
    check(
        condition=QrRenderer.modules(text=Constants.url)
        != QrRenderer.modules(text=other),
        reason="two texts must not share a symbol",
    )
    expect(actual=QrRenderer.width(text=Constants.url) > 0, expected=True)


def test_plain_drawing_uses_two_module_rows_per_line() -> None:
    """Plain output is block characters only, one column per module."""
    rows: tuple[tuple[bool, ...], ...] = QrRenderer.modules(text=Constants.url)
    lines: tuple[str, ...] = QrRenderer.render(text=Constants.url, color=False)
    expect(actual=len(lines), expected=ceil(len(rows) / 2))
    for line in lines:
        expect(actual=len(line), expected=len(rows[0]))
        check(
            condition=set(line) <= Constants.plain_glyphs,
            reason=f"unexpected glyphs: {set(line) - Constants.plain_glyphs}",
        )
    expect(actual=lines[0], expected="█" * len(rows[0]))


def test_coloured_drawing_forces_black_on_white() -> None:
    """Colour output does not depend on the terminal's theme."""
    lines: tuple[str, ...] = QrRenderer.render(text=Constants.url, color=True)
    width: int = QrRenderer.width(text=Constants.url)
    for line in lines:
        check(
            condition=line.endswith(QrConstants.reset),
            reason="resets colour",
        )
        expect(actual=line.count("▀"), expected=width)
    first: str = lines[0]
    white: str = (
        f"\x1b[38;5;{QrConstants.white}m\x1b[48;5;{QrConstants.white}m"
    )
    check(condition=white in first, reason="the quiet zone is white")
    check(
        condition=any(
            f"\x1b[38;5;{QrConstants.black}m" in line for line in lines
        ),
        reason="dark modules are black",
    )


def test_an_odd_number_of_module_rows_still_renders() -> None:
    """The last line pairs the final module row with a light row."""
    rows: tuple[tuple[bool, ...], ...] = QrRenderer.modules(text=Constants.url)
    lines: tuple[str, ...] = QrRenderer.render(text=Constants.url, color=False)
    expect(actual=len(lines) * 2 >= len(rows), expected=True)


def test_a_wide_terminal_puts_the_link_beside_the_code() -> None:
    """The text shares lines with the drawing and the link is unbroken."""
    banner: str = PairingBanner.render(
        url=Constants.url,
        headline=Constants.headline,
        columns=160,
        color=False,
    )
    lines: list[str] = banner.splitlines()
    beside: list[str] = [line for line in lines if Constants.url in line]
    expect(actual=len(beside), expected=1)
    check(
        condition=beside[0][0] in Constants.plain_glyphs - {" "},
        reason="the link shares a line with the QR drawing",
    )
    check(
        condition=not any(line.startswith("Link:") for line in lines),
        reason="no separate copy line is needed when the link fits",
    )
    check(condition=banner.endswith("\n"), reason="ends with a newline")
    expect(
        actual=any("Scan with your phone" in line for line in lines),
        expected=True,
    )


def test_a_medium_terminal_wraps_the_link_and_repeats_it_whole() -> None:
    """Beside the code the link wraps; a copyable line follows."""
    width: int = QrRenderer.width(text=Constants.url)
    columns: int = width + QrConstants.gap + 30
    banner: str = PairingBanner.render(
        url=Constants.url,
        headline=Constants.headline,
        columns=columns,
        color=False,
    )
    lines: list[str] = banner.splitlines()
    expect(actual=lines[-1], expected=f"Link: {Constants.url}")
    pieces: list[str] = [
        line[width + QrConstants.gap :]
        for line in lines[:-2]
        if len(line) > width
    ]
    check(
        condition=Constants.url in "".join(piece.strip() for piece in pieces),
        reason="the wrapped chunks reassemble into the link",
    )


def test_a_nbowman_terminal_stacks_the_text_under_the_code() -> None:
    """With no room beside the code, everything is printed below it."""
    banner: str = PairingBanner.render(
        url=Constants.url,
        headline=Constants.headline,
        columns=60,
        color=False,
    )
    lines: list[str] = banner.splitlines()
    expect(actual=lines[-1], expected=f"Link: {Constants.url}")
    expect(actual=lines[-3:-1], expected=list(Constants.headline))
    drawing: int = len(QrRenderer.render(text=Constants.url, color=False))
    expect(actual=lines[drawing], expected="")


def test_a_long_headline_that_would_outgrow_the_code_is_stacked() -> None:
    """Too many instruction lines for the drawing's height fall back."""
    headline: tuple[str, ...] = tuple(f"line {index}" for index in range(60))
    banner: str = PairingBanner.render(
        url=Constants.url,
        headline=headline,
        columns=200,
        color=False,
    )
    expect(actual=banner.splitlines()[-1], expected=f"Link: {Constants.url}")


def test_every_supported_matrix_decodes_with_opencv() -> None:
    """Decode every EC/version/mask combination with a real QR decoder."""
    text: str = "QR"
    detector: QRCodeDetector = QRCodeDetector()
    for error_correction in ErrorCorrection:
        for version in range(1, 11):
            for mask in range(8):
                symbol: QrCode = QrCode(
                    data=text.encode(encoding="utf-8"),
                    error_correction=error_correction,
                    version=version,
                    mask=mask,
                )
                modules: ndarray[
                    tuple[int, int],
                    dtype[uint8],
                ] = asarray(a=symbol.modules, dtype=uint8)
                quiet: ndarray[
                    tuple[int, int],
                    dtype[uint8],
                ] = pad(array=modules, pad_width=4, constant_values=0)
                pixels: ndarray[
                    tuple[int, int],
                    dtype[uint8],
                ] = where(quiet != 0, 0, 255).astype(dtype=uint8)
                decoded: str = ""
                for scale in (3, 4, 5, 6, 8):
                    decoded, _, _ = detector.detectAndDecode(
                        image := repeat(
                            a=repeat(a=pixels, repeats=scale, axis=0),
                            repeats=scale,
                            axis=1,
                        ),
                    )
                    if decoded == text:
                        break
                    framed: MatLike = copyMakeBorder(
                        image,
                        1,
                        2,
                        3,
                        4,
                        BORDER_CONSTANT,
                        value=255,
                    )
                    decoded, _, _ = detector.detectAndDecode(framed)
                    if decoded == text:
                        break
                expect(actual=decoded, expected=text)


def test_encoder_rejects_invalid_forced_parameters() -> None:
    """Forced versions and masks are validated before matrix generation."""
    for version in (0, 11):
        try:
            QrCode(data=b"QR", version=version)
        except ValueError:
            pass
        else:
            check(condition=False, reason="invalid version must fail")
    for mask in (-1, 8):
        try:
            QrCode(data=b"QR", mask=mask)
        except ValueError:
            pass
        else:
            check(condition=False, reason="invalid mask must fail")
    try:
        QrCode(
            data=b"payload too large for version one high correction",
            error_correction=ErrorCorrection.high,
            version=1,
        )
    except ValueError:
        pass
    else:
        check(condition=False, reason="oversized forced version must fail")


def test_dependency_free_encoder_support_utilities_and_renderers() -> None:
    """Exercise encoder utilities and every terminal rendering path."""
    buffer: BitBuffer = BitBuffer()
    buffer.append_bits(bits=(1, 0, 1, 0, 0, 1, 0, 1))
    expect(actual=buffer.bits, expected=(1, 0, 1, 0, 0, 1, 0, 1))
    expect(actual=buffer.to_bytes(), expected=[0xA5])
    unaligned: BitBuffer = BitBuffer()
    unaligned.append(value=1, length=1)
    try:
        unaligned.to_bytes()
    except ValueError:
        pass
    else:
        check(condition=False, reason="unaligned bits must fail")
    expect(actual=Galois.multiply(left=0, right=17), expected=0)
    symbol: QrCode = QrCode(data=b"render")
    expect(actual=symbol.version >= 1, expected=True)
    for ansi in (False, True):
        for compact in (False, True):
            rendered: str = symbol.render_terminal(
                border=1,
                compact=compact,
                invert=True,
                ansi=ansi,
            )
            check(condition=bool(rendered), reason="renderer returned text")
    try:
        symbol.render_terminal(border=-1)
    except ValueError:
        pass
    else:
        check(condition=False, reason="negative border must fail")


def test_automatic_version_rejects_payload_beyond_version_ten() -> None:
    """Automatic selection fails when no supported version can hold data."""
    try:
        QrCode(data=b"x" * 3000)
    except ValueError:
        pass
    else:
        check(condition=False, reason="oversized payload must fail")
