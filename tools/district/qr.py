# Copyright (c) 2026 Project contributors.
"""Dependency-free QR code generation for terminal output."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from textwrap import wrap
from typing import TYPE_CHECKING, Final, Protocol, final

if TYPE_CHECKING:
    from collections.abc import Sequence


@final
class EncoderConstants:
    """Group QR constants.

    Attributes:
        max_version: Highest QR version supported by the encoder.
        min_version: Lowest QR version supported by the encoder.
        pad_bytes: Alternating bytes used to fill unused data capacity.
        gf_primitive: Primitive polynomial used for GF(256) arithmetic.
        format_generator: BCH generator polynomial for format information.
        format_mask: XOR mask applied to QR format information.
        version_generator: BCH generator polynomial for version information.
        alphanumeric: Character set defined by QR alphanumeric mode.
        byte_count_version_limit: Last version using an 8-bit byte count.
        version_information_minimum: First version carrying version bits.
        timing_pattern_coordinate: Row and column of the timing patterns.
        penalty_run_length: Run length at which mask penalties begin.
        finder_guard_modules: Required light guard modules around finder-like
            penalty patterns.
        finder_pattern_length: Module length of a finder-like penalty pattern.
        finder_pattern_span: Finder-like pattern plus its required guard span.

    """

    max_version: Final[int] = 10
    min_version: Final[int] = 1
    pad_bytes: Final[tuple[int, int]] = (0xEC, 0x11)
    gf_primitive: Final[int] = 0x11D
    format_generator: Final[int] = 0x537
    format_mask: Final[int] = 0x5412
    version_generator: Final[int] = 0x1F25
    alphanumeric: Final[str] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
    byte_count_version_limit: Final[int] = 9
    version_information_minimum: Final[int] = 7
    timing_pattern_coordinate: Final[int] = 6
    penalty_run_length: Final[int] = 5
    finder_guard_modules: Final[int] = 4
    finder_pattern_length: Final[int] = 7
    finder_pattern_span: Final[int] = 11


class ModuleReader(Protocol):
    """Read one QR module coordinate."""

    def __call__(self, *, x: int, y: int) -> bool:
        """Return whether the requested module is dark.

        Args:
            x: Horizontal module coordinate.
            y: Vertical module coordinate.

        Returns:
            Whether the module is dark.

        """
        ...


class ErrorCorrection(IntEnum):
    """QR error-correction levels.

    Attributes:
        low: Low error correction, approximately seven percent recovery.
        medium: Medium error correction, approximately fifteen percent.
        quartile: Quartile error correction, approximately twenty-five percent.
        high: High error correction, approximately thirty percent recovery.

    """

    low = 1
    medium = 0
    quartile = 3
    high = 2


@dataclass(frozen=True, slots=True, kw_only=True)
class BlockSpec:
    """Describe one Reed-Solomon block group.

    Attributes:
        count: Number of blocks using this specification.
        total: Total codewords in each block.
        data: Data codewords in each block.

    """

    count: int
    total: int
    data: int


@final
class Tables:
    """Group QR capacity/block tables for versions 1 through 10.

    Attributes:
        blocks: Reed-Solomon block specifications by level and version.
        alignments: Alignment-pattern centre coordinates by version.

    """

    blocks: Final[dict[ErrorCorrection, tuple[tuple[BlockSpec, ...], ...]]] = {
        ErrorCorrection.low: (
            (BlockSpec(count=1, total=26, data=19),),
            (BlockSpec(count=1, total=44, data=34),),
            (BlockSpec(count=1, total=70, data=55),),
            (BlockSpec(count=1, total=100, data=80),),
            (BlockSpec(count=1, total=134, data=108),),
            (BlockSpec(count=2, total=86, data=68),),
            (BlockSpec(count=2, total=98, data=78),),
            (BlockSpec(count=2, total=121, data=97),),
            (BlockSpec(count=2, total=146, data=116),),
            (
                BlockSpec(count=2, total=86, data=68),
                BlockSpec(count=2, total=87, data=69),
            ),
        ),
        ErrorCorrection.medium: (
            (BlockSpec(count=1, total=26, data=16),),
            (BlockSpec(count=1, total=44, data=28),),
            (BlockSpec(count=1, total=70, data=44),),
            (BlockSpec(count=2, total=50, data=32),),
            (BlockSpec(count=2, total=67, data=43),),
            (BlockSpec(count=4, total=43, data=27),),
            (BlockSpec(count=4, total=49, data=31),),
            (
                BlockSpec(count=2, total=60, data=38),
                BlockSpec(count=2, total=61, data=39),
            ),
            (
                BlockSpec(count=3, total=58, data=36),
                BlockSpec(count=2, total=59, data=37),
            ),
            (
                BlockSpec(count=4, total=69, data=43),
                BlockSpec(count=1, total=70, data=44),
            ),
        ),
        ErrorCorrection.quartile: (
            (BlockSpec(count=1, total=26, data=13),),
            (BlockSpec(count=1, total=44, data=22),),
            (BlockSpec(count=2, total=35, data=17),),
            (BlockSpec(count=2, total=50, data=24),),
            (
                BlockSpec(count=2, total=33, data=15),
                BlockSpec(count=2, total=34, data=16),
            ),
            (BlockSpec(count=4, total=43, data=19),),
            (
                BlockSpec(count=2, total=32, data=14),
                BlockSpec(count=4, total=33, data=15),
            ),
            (
                BlockSpec(count=4, total=40, data=18),
                BlockSpec(count=2, total=41, data=19),
            ),
            (
                BlockSpec(count=4, total=36, data=16),
                BlockSpec(count=4, total=37, data=17),
            ),
            (
                BlockSpec(count=6, total=43, data=19),
                BlockSpec(count=2, total=44, data=20),
            ),
        ),
        ErrorCorrection.high: (
            (BlockSpec(count=1, total=26, data=9),),
            (BlockSpec(count=1, total=44, data=16),),
            (BlockSpec(count=2, total=35, data=13),),
            (BlockSpec(count=4, total=25, data=9),),
            (
                BlockSpec(count=2, total=33, data=11),
                BlockSpec(count=2, total=34, data=12),
            ),
            (BlockSpec(count=4, total=43, data=15),),
            (
                BlockSpec(count=4, total=39, data=13),
                BlockSpec(count=1, total=40, data=14),
            ),
            (
                BlockSpec(count=4, total=40, data=14),
                BlockSpec(count=2, total=41, data=15),
            ),
            (
                BlockSpec(count=4, total=36, data=12),
                BlockSpec(count=4, total=37, data=13),
            ),
            (
                BlockSpec(count=6, total=43, data=15),
                BlockSpec(count=2, total=44, data=16),
            ),
        ),
    }
    alignments: Final[tuple[tuple[int, ...], ...]] = (
        (),
        (6, 18),
        (6, 22),
        (6, 26),
        (6, 30),
        (6, 34),
        (6, 22, 38),
        (6, 24, 42),
        (6, 26, 46),
        (6, 28, 50),
    )


class BitBuffer:
    """Accumulate individual QR data bits."""

    def __init__(self) -> None:
        """Initialize an empty bit buffer."""
        self._bits: list[int] = []

    @property
    def length(self) -> int:
        """Return bit count.

        Returns:
            Number of bits currently stored.

        """
        return len(self._bits)

    @property
    def bits(self) -> tuple[int, ...]:
        """Return immutable bits.

        Returns:
            Stored bits as an immutable tuple.

        """
        return tuple(self._bits)

    def append(self, *, value: int, length: int) -> None:
        """Append the low ``length`` bits of an integer.

        Args:
            value: Integer whose low-order bits are appended.
            length: Number of bits to append.

        """
        for shift in range(length - 1, -1, -1):
            self._bits.append((value >> shift) & 1)

    def append_bits(self, *, bits: tuple[int, ...]) -> None:
        """Append existing bits.

        Args:
            bits: Bits to append in order.

        """
        self._bits.extend(bits)

    def to_bytes(self) -> list[int]:
        """Pack bits into bytes.

        Returns:
            Packed bytes represented as integer values.

        Raises:
            ValueError: If the buffer length is not byte aligned.

        """
        if self.length % 8:
            msg: str = "Bit buffer is not byte aligned."
            raise ValueError(msg)
        return [
            sum(
                self._bits[index + offset] << (7 - offset)
                for offset in range(8)
            )
            for index in range(0, self.length, 8)
        ]


@final
class Galois:
    """Perform QR Reed-Solomon arithmetic over GF(256).

    Attributes:
        exp: Exponent lookup table for GF(256).
        log: Logarithm lookup table for GF(256).
        initialized: Whether the lookup tables have been populated.

    """

    exp: list[int] = [0] * 512
    log: list[int] = [0] * 256
    initialized: bool = False

    @classmethod
    def initialize(cls) -> None:
        """Initialize logarithm/exponent lookup tables."""
        if cls.initialized:
            return
        value: int = 1
        for index in range(255):
            cls.exp[index] = value
            cls.log[value] = index
            value <<= 1
            if value & 0x100:
                value ^= EncoderConstants.gf_primitive
        for index in range(255, 512):
            cls.exp[index] = cls.exp[index - 255]
        cls.initialized = True

    @classmethod
    def multiply(cls, *, left: int, right: int) -> int:
        """Multiply two GF(256) values.

        Args:
            left: Left GF(256) factor.
            right: Right GF(256) factor.

        Returns:
            Product in GF(256).

        """
        if left == 0 or right == 0:
            return 0
        cls.initialize()
        return cls.exp[cls.log[left] + cls.log[right]]

    @classmethod
    def generator(cls, *, degree: int) -> list[int]:
        """Build a Reed-Solomon generator polynomial.

        Args:
            degree: Degree of the generator polynomial.

        Returns:
            Generator polynomial coefficients.

        """
        polynomial: list[int] = [1]
        for index in range(degree):
            root: int = cls.exp[index] if cls.initialized else 0
            if not cls.initialized:
                cls.initialize()
                root = cls.exp[index]
            next_polynomial: list[int] = [0] * (len(polynomial) + 1)
            for position, coefficient in enumerate(iterable=polynomial):
                next_polynomial[position] ^= coefficient
                next_polynomial[position + 1] ^= cls.multiply(
                    left=coefficient,
                    right=root,
                )
            polynomial = next_polynomial
        return polynomial

    @classmethod
    def remainder(
        cls,
        *,
        data: list[int],
        degree: int,
    ) -> list[int]:
        """Return Reed-Solomon remainder bytes.

        Args:
            data: Data codewords to protect.
            degree: Number of error-correction codewords to produce.

        Returns:
            Reed-Solomon remainder codewords.

        """
        generator: list[int] = cls.generator(degree=degree)
        result: list[int] = [*data, *([0] * degree)]
        for index in range(len(data)):
            factor: int = result[index]
            if factor == 0:
                continue
            for offset, coefficient in enumerate(iterable=generator):
                result[index + offset] ^= cls.multiply(
                    left=coefficient,
                    right=factor,
                )
        return result[-degree:]


class QrCode:
    """Generate a standards-compatible byte-mode QR matrix."""

    def __init__(
        self,
        *,
        data: bytes,
        error_correction: ErrorCorrection = ErrorCorrection.medium,
        version: int | None = None,
        mask: int | None = None,
    ) -> None:
        """Encode data into a QR matrix.

        Args:
            data: Byte-mode payload.
            error_correction: QR error-correction level.
            version: Exact QR version, or ``None`` to select the smallest fit.
            mask: Exact mask from zero through seven, or ``None`` to select
                the lowest-penalty mask.

        Raises:
            ValueError: If a forced version or mask is invalid, or the data
                does not fit the requested version.

        """
        self._data: bytes = data
        self._error_correction: ErrorCorrection = error_correction
        self._version: int = self._resolve_version(version=version)
        self._forced_mask: int | None = self._resolve_mask(mask=mask)
        self._size: int = 17 + 4 * self._version
        self._modules: list[list[bool]] = []
        self._function: list[list[bool]] = []
        codewords: list[int] = self._make_codewords()
        self._build(codewords=codewords)

    @property
    def version(self) -> int:
        """Return selected QR version.

        Returns:
            Selected QR version.

        """
        return self._version

    @property
    def size(self) -> int:
        """Return matrix side length.

        Returns:
            Number of modules along one matrix side.

        """
        return self._size

    @property
    def modules(self) -> tuple[tuple[bool, ...], ...]:
        """Return immutable QR modules.

        Returns:
            Immutable rows of QR modules.

        """
        return tuple(tuple(row) for row in self._modules)

    def render_terminal(
        self,
        *,
        border: int = 2,
        compact: bool = True,
        invert: bool = False,
        ansi: bool = True,
    ) -> str:
        """Render the QR code in the terminal.

        ANSI mode paints terminal cell backgrounds, avoiding the visible
        seams that some fonts produce between adjacent block glyphs. Compact
        ANSI mode uses foreground/background colors with the upper-half block
        so each terminal row represents two QR module rows.

        Args:
            border: Quiet-zone width in QR modules.
            compact: Render two QR module rows per terminal row.
            invert: Swap dark and light modules.
            ansi: Use seamless ANSI foreground/background rendering.

        Returns:
            Terminal-ready QR code text.

        Raises:
            ValueError: If ``border`` is negative.

        """
        if border < 0:
            msg: str = "border must be non-negative."
            raise ValueError(msg)

        def module(*, x: int, y: int) -> bool:
            """Read one rendered module, including the quiet zone.

            Args:
                x: Horizontal module coordinate.
                y: Vertical module coordinate.

            Returns:
                Whether the rendered module is dark.

            """
            if x < 0 or y < 0 or x >= self._size or y >= self._size:
                return False
            value: bool = self._modules[y][x]
            return not value if invert else value

        if not ansi:
            return self._render_blocks(
                module=module,
                border=border,
                compact=compact,
            )
        return self._render_ansi(
            module=module,
            border=border,
            compact=compact,
        )

    @staticmethod
    def _resolve_mask(*, mask: int | None) -> int | None:
        """Validate an optional forced QR mask.

        Args:
            mask: Requested mask, or ``None`` for automatic selection.

        Returns:
            The validated mask or ``None``.

        Raises:
            ValueError: If the mask is outside zero through seven.

        """
        max_mask: int = 7
        if mask is not None and not 0 <= mask <= max_mask:
            msg: str = "mask must be between 0 and 7."
            raise ValueError(msg)
        return mask

    @staticmethod
    def _interleave_blocks(*, blocks: list[list[int]]) -> list[int]:
        """Interleave uneven QR blocks column by column.

        Args:
            blocks: Codeword blocks to interleave.

        Returns:
            Interleaved codewords.

        """
        width: int = max(len(block) for block in blocks)
        return [
            block[index]
            for index in range(width)
            for block in blocks
            if index < len(block)
        ]

    @staticmethod
    def _mask(*, mask: int, x: int, y: int) -> bool:
        """Return whether a mask toggles one data module.

        Args:
            mask: QR mask index.
            x: Horizontal module coordinate.
            y: Vertical module coordinate.

        Returns:
            Whether the selected mask toggles the module.

        """
        formulas: tuple[bool, ...] = (
            (x + y) % 2 == 0,
            y % 2 == 0,
            x % 3 == 0,
            (x + y) % 3 == 0,
            (x // 3 + y // 2) % 2 == 0,
            x * y % 2 + x * y % 3 == 0,
            (x * y % 2 + x * y % 3) % 2 == 0,
            ((x + y) % 2 + x * y % 3) % 2 == 0,
        )
        return formulas[mask]

    @staticmethod
    def _run_penalty(*, line: list[bool]) -> int:
        """Return the adjacent-module run penalty for one matrix line.

        Args:
            line: Matrix row or column to score.

        Returns:
            Adjacent-module run penalty.

        """
        penalty: int = 0
        run_color: bool = line[0]
        run_length: int = 1
        for color in line[1:]:
            if color != run_color:
                run_color = color
                run_length = 1
                continue
            run_length += 1
            if run_length == EncoderConstants.penalty_run_length:
                penalty += 3
            elif run_length > EncoderConstants.penalty_run_length:
                penalty += 1
        return penalty

    @staticmethod
    def _finder_penalty(*, line: list[bool]) -> int:
        """Return the finder-like pattern penalty for one matrix line.

        Args:
            line: Matrix row or column to score.

        Returns:
            Finder-like pattern penalty.

        """
        pattern: tuple[bool, ...] = (
            True,
            False,
            True,
            True,
            True,
            False,
            True,
        )
        penalty: int = 0
        pattern_length: int = EncoderConstants.finder_pattern_length
        guard: int = EncoderConstants.finder_guard_modules
        span: int = EncoderConstants.finder_pattern_span
        for index in range(len(line) - pattern_length + 1):
            if tuple(line[index : index + pattern_length]) != pattern:
                continue
            before: bool = index >= guard and not any(
                line[index - guard : index],
            )
            after: bool = index + span <= len(line) and not any(
                line[index + pattern_length : index + span],
            )
            if before or after:
                penalty += 40
        return penalty

    def _resolve_version(self, *, version: int | None) -> int:
        """Validate or automatically select the QR version.

        Args:
            version: Requested version, or ``None`` for automatic selection.

        Returns:
            Validated or automatically selected QR version.

        Raises:
            ValueError: If the version is unsupported or cannot hold data.

        """
        if version is None:
            return self._choose_version()
        if (
            not EncoderConstants.min_version
            <= version
            <= EncoderConstants.max_version
        ):
            msg: str = (
                f"version must be between {EncoderConstants.min_version} and "
                f"{EncoderConstants.max_version}."
            )
            raise ValueError(msg)
        count_bits: int = (
            8 if version <= EncoderConstants.byte_count_version_limit else 16
        )
        required: int = 4 + count_bits + len(self._data) * 8
        if required > self._data_capacity(version=version) * 8:
            msg = f"Input does not fit QR version {version}."
            raise ValueError(msg)
        return version

    def _render_ansi(
        self,
        *,
        module: ModuleReader,
        border: int,
        compact: bool,
    ) -> str:
        """Render using ANSI-painted terminal cells.

        Args:
            module: Callable that reads one QR module.
            border: Quiet-zone width in modules.
            compact: Render two module rows per terminal row.

        Returns:
            ANSI terminal representation.

        """
        dark_fg: str = "\x1b[30m"
        light_fg: str = "\x1b[97m"
        dark_bg: str = "\x1b[40m"
        light_bg: str = "\x1b[107m"
        reset: str = "\x1b[0m"

        def value(*, x: int, y: int) -> bool:
            """Read one module through the renderer callback.

            Args:
                x: Horizontal module coordinate.
                y: Vertical module coordinate.

            Returns:
                Whether the module is dark.

            """
            return module(x=x, y=y)

        x_start: int = -border
        x_end: int = self._size + border
        y_start: int = -border
        y_end: int = self._size + border
        if not compact:
            lines: list[str] = []
            for y in range(y_start, y_end):
                parts: list[str] = []
                previous: bool | None = None
                for x in range(x_start, x_end):
                    dark: bool = value(x=x, y=y)
                    if dark != previous:
                        parts.append(dark_bg if dark else light_bg)
                        previous = dark
                    parts.append("  ")
                parts.append(reset)
                lines.append("".join(parts))
            return "\n".join(lines)
        lines = []
        y: int = y_start
        while y < y_end:
            parts = []
            for x in range(x_start, x_end):
                top: bool = value(x=x, y=y)
                bottom: bool = value(x=x, y=y + 1) if y + 1 < y_end else False
                foreground: str = light_fg if not top else dark_fg
                background: str = dark_bg if bottom else light_bg
                parts.extend((foreground, background, "▀"))
            parts.append(reset)
            lines.append("".join(parts))
            y += 2
        return "\n".join(lines)

    def _render_blocks(
        self,
        *,
        module: ModuleReader,
        border: int,
        compact: bool,
    ) -> str:
        """Render using block glyphs only, without ANSI colors.

        Args:
            module: Callable that reads one QR module.
            border: Quiet-zone width in modules.
            compact: Render two module rows per terminal row.

        Returns:
            Plain block-glyph terminal representation.

        """

        def value(*, x: int, y: int) -> bool:
            """Read one module through the renderer callback.

            Args:
                x: Horizontal module coordinate.
                y: Vertical module coordinate.

            Returns:
                Whether the module is dark.

            """
            return module(x=x, y=y)

        x_start: int = -border
        x_end: int = self._size + border
        y_start: int = -border
        y_end: int = self._size + border
        if not compact:
            return "\n".join(
                "".join(
                    "██" if value(x=x, y=y) else "  "
                    for x in range(x_start, x_end)
                )
                for y in range(y_start, y_end)
            )
        lines: list[str] = []
        y: int = y_start
        while y < y_end:
            characters: list[str] = []
            for x in range(x_start, x_end):
                top: bool = value(x=x, y=y)
                bottom: bool = value(x=x, y=y + 1) if y + 1 < y_end else False
                if top and bottom:
                    character: str = "█"
                elif top:
                    character = "▀"
                elif bottom:
                    character = "▄"
                else:
                    character = " "
                characters.append(character)
            lines.append("".join(characters))
            y += 2
        return "\n".join(lines)

    def _choose_version(self) -> int:
        """Select the smallest supported version that fits the payload.

        Returns:
            Smallest QR version capable of storing the payload.

        Raises:
            ValueError: If the payload exceeds the supported versions.

        """
        for version in range(
            EncoderConstants.min_version,
            EncoderConstants.max_version + 1,
        ):
            capacity: int = self._data_capacity(version=version)
            count_bits: int = (
                8
                if version <= EncoderConstants.byte_count_version_limit
                else 16
            )
            required: int = 4 + count_bits + len(self._data) * 8
            if required <= capacity * 8:
                return version
        msg: str = (
            "Input is too long for this dependency-free encoder "
            f"(maximum supported QR version: {EncoderConstants.max_version})."
        )
        raise ValueError(
            msg,
        )

    def _data_capacity(self, *, version: int) -> int:
        """Return the data-codeword capacity for a QR version.

        Args:
            version: QR version to inspect.

        Returns:
            Number of available data codewords.

        """
        return sum(
            spec.count * spec.data
            for spec in Tables.blocks[self._error_correction][version - 1]
        )

    def _make_data_bytes(self, *, data_capacity: int) -> list[int]:
        """Create padded byte-mode data bytes for the selected version.

        Args:
            data_capacity: Available data codewords.

        Returns:
            Padded byte-mode data codewords.

        """
        buffer: BitBuffer = BitBuffer()
        buffer.append(value=0b0100, length=4)
        buffer.append(
            value=len(self._data),
            length=(
                8
                if self._version <= EncoderConstants.byte_count_version_limit
                else 16
            ),
        )
        for value in self._data:
            buffer.append(value=value, length=8)
        capacity_bits: int = data_capacity * 8
        buffer.append(value=0, length=min(4, capacity_bits - buffer.length))
        while buffer.length % 8:
            buffer.append(value=0, length=1)
        data_bytes: list[int] = buffer.to_bytes()
        pad_index: int = 0
        while len(data_bytes) < data_capacity:
            data_bytes.append(EncoderConstants.pad_bytes[pad_index % 2])
            pad_index += 1
        return data_bytes

    def _make_blocks(
        self,
        *,
        data_bytes: list[int],
    ) -> tuple[list[list[int]], list[list[int]]]:
        """Split data bytes into QR data and error-correction blocks.

        Args:
            data_bytes: Padded data codewords to split into blocks.

        Returns:
            Data blocks and their corresponding error-correction blocks.

        """
        data_blocks: list[list[int]] = []
        ecc_blocks: list[list[int]] = []
        cursor: int = 0
        specs: tuple[BlockSpec, ...] = Tables.blocks[self._error_correction][
            self._version - 1
        ]
        for spec in specs:
            for _ in range(spec.count):
                block: list[int] = data_bytes[cursor : cursor + spec.data]
                cursor += spec.data
                data_blocks.append(block)
                ecc_blocks.append(
                    Galois.remainder(
                        data=block,
                        degree=spec.total - spec.data,
                    ),
                )
        return data_blocks, ecc_blocks

    def _make_codewords(self) -> list[int]:
        """Create interleaved data and error-correction codewords.

        Returns:
            Interleaved data and error-correction codewords.

        """
        data_capacity: int = self._data_capacity(version=self._version)
        data_bytes: list[int] = self._make_data_bytes(
            data_capacity=data_capacity,
        )
        data_blocks, ecc_blocks = self._make_blocks(data_bytes=data_bytes)
        return self._interleave_blocks(
            blocks=data_blocks,
        ) + self._interleave_blocks(blocks=ecc_blocks)

    def _build(self, *, codewords: list[int]) -> None:
        """Build the matrix using the lowest-penalty mask.

        Args:
            codewords: Interleaved data and error-correction codewords.

        Raises:
            RuntimeError: If no QR mask can be selected.

        """
        best_modules: list[list[bool]] | None = None
        best_penalty: int | None = None
        masks: range | tuple[int, ...] = (
            range(8) if self._forced_mask is None else (self._forced_mask,)
        )
        for mask in masks:
            self._modules = [[False] * self._size for _ in range(self._size)]
            self._function = [[False] * self._size for _ in range(self._size)]
            self._draw_function_patterns(mask=mask)
            self._draw_codewords(codewords=codewords, mask=mask)
            penalty: int = self._penalty()
            if best_penalty is None or penalty < best_penalty:
                best_penalty = penalty
                best_modules = [row[:] for row in self._modules]
        if best_modules is None:
            msg: str = "Unable to select QR mask."
            raise RuntimeError(msg)
        self._modules = best_modules

    def _set_function(self, *, x: int, y: int, dark: bool) -> None:
        """Set one in-bounds function module.

        Args:
            x: Horizontal module coordinate.
            y: Vertical module coordinate.
            dark: Whether the module is dark.

        """
        if 0 <= x < self._size and 0 <= y < self._size:
            self._modules[y][x] = dark
            self._function[y][x] = True

    def _draw_function_patterns(self, *, mask: int) -> None:
        """Draw all fixed QR function patterns.

        Args:
            mask: QR mask index used by the format information.

        """
        for index in range(self._size):
            self._set_function(
                x=6,
                y=index,
                dark=index % 2 == 0,
            )
            self._set_function(
                x=index,
                y=6,
                dark=index % 2 == 0,
            )
        self._draw_finder(center_x=3, center_y=3)
        self._draw_finder(center_x=self._size - 4, center_y=3)
        self._draw_finder(center_x=3, center_y=self._size - 4)
        positions: tuple[int, ...] = Tables.alignments[self._version - 1]
        if positions:
            last: int = positions[-1]
            finder_centers: frozenset[tuple[int, int]] = frozenset(
                {
                    (
                        EncoderConstants.timing_pattern_coordinate,
                        EncoderConstants.timing_pattern_coordinate,
                    ),
                    (EncoderConstants.timing_pattern_coordinate, last),
                    (last, EncoderConstants.timing_pattern_coordinate),
                },
            )
            for center_y in positions:
                for center_x in positions:
                    if (center_x, center_y) in finder_centers:
                        continue
                    self._draw_alignment(
                        center_x=center_x,
                        center_y=center_y,
                    )
        self._draw_format_bits(mask=mask)
        if self._version >= EncoderConstants.version_information_minimum:
            self._draw_version_bits()

    def _draw_finder(self, *, center_x: int, center_y: int) -> None:
        """Draw one finder pattern and separator.

        Args:
            center_x: Horizontal finder centre.
            center_y: Vertical finder centre.

        """
        for delta_y in range(-4, 5):
            for delta_x in range(-4, 5):
                distance: int = max(abs(delta_x), abs(delta_y))
                self._set_function(
                    x=center_x + delta_x,
                    y=center_y + delta_y,
                    dark=distance not in {2, 4},
                )

    def _draw_alignment(
        self,
        *,
        center_x: int,
        center_y: int,
    ) -> None:
        """Draw one alignment pattern.

        Args:
            center_x: Horizontal alignment centre.
            center_y: Vertical alignment centre.

        """
        for delta_y in range(-2, 3):
            for delta_x in range(-2, 3):
                self._set_function(
                    x=center_x + delta_x,
                    y=center_y + delta_y,
                    dark=max(abs(delta_x), abs(delta_y)) != 1,
                )

    def _draw_format_bits(self, *, mask: int) -> None:
        """Draw BCH-protected error-correction and mask information.

        Args:
            mask: QR mask index encoded into the format information.

        """
        data: int = (int(self._error_correction) << 3) | mask
        remainder: int = data << 10
        for shift in range(14, 9, -1):
            if (remainder >> shift) & 1:
                remainder ^= EncoderConstants.format_generator << (shift - 10)
        bits: int = ((data << 10) | remainder) ^ EncoderConstants.format_mask
        for index in range(6):
            self._set_function(
                x=8,
                y=index,
                dark=((bits >> index) & 1) != 0,
            )
        self._set_function(
            x=8,
            y=7,
            dark=((bits >> 6) & 1) != 0,
        )
        self._set_function(
            x=8,
            y=8,
            dark=((bits >> 7) & 1) != 0,
        )
        self._set_function(
            x=7,
            y=8,
            dark=((bits >> 8) & 1) != 0,
        )
        for index in range(9, 15):
            self._set_function(
                x=14 - index,
                y=8,
                dark=((bits >> index) & 1) != 0,
            )
        for index in range(8):
            self._set_function(
                x=self._size - 1 - index,
                y=8,
                dark=((bits >> index) & 1) != 0,
            )
        for index in range(8, 15):
            self._set_function(
                x=8,
                y=self._size - 15 + index,
                dark=((bits >> index) & 1) != 0,
            )
        self._set_function(x=8, y=self._size - 8, dark=True)

    def _draw_version_bits(self) -> None:
        """Draw BCH-protected version information for version seven up."""
        remainder: int = self._version << 12
        for shift in range(17, 11, -1):
            if (remainder >> shift) & 1:
                remainder ^= EncoderConstants.version_generator << (shift - 12)
        bits: int = (self._version << 12) | remainder
        for index in range(18):
            dark: bool = ((bits >> index) & 1) != 0
            x: int = self._size - 11 + index % 3
            y: int = index // 3
            self._set_function(x=x, y=y, dark=dark)
            self._set_function(x=y, y=x, dark=dark)

    def _draw_codewords(
        self,
        *,
        codewords: list[int],
        mask: int,
    ) -> None:
        """Place masked codeword bits into non-function modules.

        Args:
            codewords: Interleaved QR codewords to place.
            mask: QR mask index applied while placing data.

        """
        bits: tuple[int, ...] = tuple(
            (value >> shift) & 1
            for value in codewords
            for shift in range(7, -1, -1)
        )
        bit_index: int = 0
        right: int = self._size - 1
        upward: bool = True
        while right >= 1:
            if right == EncoderConstants.timing_pattern_coordinate:
                right -= 1
            rows: range = (
                range(self._size - 1, -1, -1) if upward else range(self._size)
            )
            for y in rows:
                for x in (right, right - 1):
                    if self._function[y][x]:
                        continue
                    bit: bool = (
                        bool(bits[bit_index])
                        if bit_index < len(bits)
                        else False
                    )
                    if self._mask(mask=mask, x=x, y=y):
                        bit = not bit
                    self._modules[y][x] = bit
                    bit_index += 1
            upward = not upward
            right -= 2

    def _block_penalty(self) -> int:
        """Return the penalty for monochrome two-by-two blocks.

        Returns:
            Two-by-two block penalty.

        """
        return sum(
            3
            for y in range(self._size - 1)
            for x in range(self._size - 1)
            if self._modules[y][x + 1] == self._modules[y][x]
            and self._modules[y + 1][x] == self._modules[y][x]
            and self._modules[y + 1][x + 1] == self._modules[y][x]
        )

    def _balance_penalty(self) -> int:
        """Return the dark/light balance penalty.

        Returns:
            Dark/light balance penalty.

        """
        dark_count: int = sum(
            1 for row in self._modules for module in row if module
        )
        total: int = self._size * self._size
        return abs(dark_count * 20 - total * 10) // total * 10

    def _penalty(self) -> int:
        """Return the complete QR mask penalty score.

        Returns:
            Complete mask penalty score.

        """
        rows: list[list[bool]] = self._modules
        columns: list[list[bool]] = [
            [self._modules[y][x] for y in range(self._size)]
            for x in range(self._size)
        ]
        lines: list[list[bool]] = [*rows, *columns]
        return (
            sum(self._run_penalty(line=line) for line in lines)
            + self._block_penalty()
            + sum(self._finder_penalty(line=line) for line in lines)
            + self._balance_penalty()
        )


@final
class QrConstants:
    """Group QR-rendering constants.

    Attributes:
        border: Light modules around the symbol (the QR "quiet zone").
        gap: Columns between the symbol and the text beside it.
        black: ANSI 256-color index of a dark module.
        white: ANSI 256-color index of a light module.
        reset: ANSI sequence that ends a coloured line.
        half_blocks: Plain-text glyph per ``(top dark, bottom dark)`` pair.
        min_beside: Narrowest text column worth placing beside the symbol.

    """

    border: Final[int] = 2
    gap: Final[int] = 3
    black: Final[int] = 16
    white: Final[int] = 231
    reset: Final[str] = "\x1b[0m"
    half_blocks: Final[dict[tuple[bool, bool], str]] = {
        (False, False): "█",
        (True, False): "▄",
        (False, True): "▀",
        (True, True): " ",
    }
    min_beside: Final[int] = 24


@final
class QrRenderer:
    """Turn text into the lines of a scannable terminal QR code."""

    @classmethod
    def render(cls, *, text: str, color: bool) -> tuple[str, ...]:
        """Draw a QR code two module rows per terminal line.

        Args:
            text: Content of the code.
            color: Use explicit black and white so the code scans on any
                terminal theme; otherwise use plain block characters.

        Returns:
            The lines of the drawing, all the same visible width.

        """
        rows: tuple[tuple[bool, ...], ...] = cls.modules(text=text)
        blank: tuple[bool, ...] = (False,) * len(rows[0])
        lines: list[str] = []
        for index in range(0, len(rows), 2):
            top: tuple[bool, ...] = rows[index]
            bottom: tuple[bool, ...] = (
                rows[index + 1] if index + 1 < len(rows) else blank
            )
            cells: list[str] = [
                cls._cell(top=above, bottom=below, color=color)
                for above, below in zip(top, bottom, strict=True)
            ]
            lines.append(
                "".join(cells) + (QrConstants.reset if color else ""),
            )
        return tuple(lines)

    @classmethod
    def width(cls, *, text: str) -> int:
        """Return how many terminal columns the drawing needs.

        Args:
            text: Content of the code.

        Returns:
            The width in columns.

        """
        return len(cls.modules(text=text)[0])

    @staticmethod
    def modules(*, text: str) -> tuple[tuple[bool, ...], ...]:
        """Encode text and pad the symbol with its quiet zone.

        Args:
            text: Content of the code.

        Returns:
            Rows of modules, ``True`` for a dark module.

        """
        symbol: QrCode = QrCode(
            data=text.encode(encoding="utf-8"),
            error_correction=ErrorCorrection.medium,
        )
        margin: tuple[bool, ...] = (False,) * QrConstants.border
        width: int = symbol.size + 2 * QrConstants.border
        blank: tuple[bool, ...] = (False,) * width
        rows: list[tuple[bool, ...]] = [blank] * QrConstants.border
        rows.extend(margin + row + margin for row in symbol.modules)
        rows.extend([blank] * QrConstants.border)
        return tuple(rows)

    @staticmethod
    def _cell(*, top: bool, bottom: bool, color: bool) -> str:
        """Draw one terminal cell holding two vertically stacked modules.

        Args:
            top: Whether the upper module is dark.
            bottom: Whether the lower module is dark.
            color: Use explicit colours.

        Returns:
            The cell's text.

        """
        if not color:
            return QrConstants.half_blocks[(top, bottom)]
        foreground: int = QrConstants.black if top else QrConstants.white
        background: int = QrConstants.black if bottom else QrConstants.white
        return f"\x1b[38;5;{foreground}m\x1b[48;5;{background}m▀"


@final
class PairingBanner:
    """Compose the QR code, instructions and link for the terminal."""

    @classmethod
    def render(
        cls,
        *,
        url: str,
        headline: Sequence[str],
        columns: int,
        color: bool,
    ) -> str:
        """Place text beside the QR code, or below it on a narrow terminal.

        Args:
            url: The pairing link the code carries.
            headline: Instruction lines shown above the link.
            columns: Width of the terminal.
            color: Draw the code with explicit colours.

        Returns:
            The text to print, ending with a newline.

        """
        drawing: tuple[str, ...] = QrRenderer.render(text=url, color=color)
        width: int = QrRenderer.width(text=url)
        room: int = columns - width - QrConstants.gap
        beside: bool = room >= QrConstants.min_beside
        link: list[str] = (
            wrap(text=url, width=room, break_long_words=True) if beside else []
        )
        notes: list[str] = [*headline, "", *link]
        copy_line: bool = len(link) != 1
        if not beside or len(notes) > len(drawing):
            return cls._stacked(drawing=drawing, headline=headline, url=url)
        start: int = (len(drawing) - len(notes)) // 2
        padding: str = " " * QrConstants.gap
        lines: list[str] = []
        for index, row in enumerate(iterable=drawing):
            slot: int = index - start
            note: str = notes[slot] if 0 <= slot < len(notes) else ""
            lines.append(f"{row}{padding}{note}" if note else row)
        if copy_line:
            lines.extend(["", f"Link: {url}"])
        return "\n".join(lines) + "\n"

    @staticmethod
    def _stacked(
        *,
        drawing: Sequence[str],
        headline: Sequence[str],
        url: str,
    ) -> str:
        """Lay the text out under the code.

        Args:
            drawing: Lines of the QR drawing.
            headline: Instruction lines.
            url: The pairing link.

        Returns:
            The text to print, ending with a newline.

        """
        return "\n".join([*drawing, "", *headline, f"Link: {url}"]) + "\n"
