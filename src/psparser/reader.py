"""Binary readers for Parasolid XT primitive field types."""

from enum import Enum
import struct
from typing import BinaryIO, Callable


class Reader:
    """Thin binary reader for Parasolid primitive field types."""

    def __init__(self, stream: BinaryIO):
        self.stream = stream

    def u8(self) -> int:
        """Read an unsigned 8-bit integer."""
        return int.from_bytes(self.stream.read(1), "big")

    def u16(self) -> int:
        """Read an unsigned 16-bit integer (big-endian)."""
        return int.from_bytes(self.stream.read(2), "big")

    def u32(self) -> int:
        """Read an unsigned 32-bit integer (big-endian)."""
        return int.from_bytes(self.stream.read(4), "big")

    def f64(self) -> float:
        """Read an IEEE-754 float64 (big-endian)."""
        return struct.unpack(">d", self.stream.read(8))[0]

    def char(self, n: int = 1) -> str:
        """Read an ASCII string of fixed byte length."""
        return self.stream.read(n).decode("ascii")

    def bool8(self) -> bool:
        """Read a logical value encoded as one byte (0 or 1)."""
        value = self.u8()
        if value not in (0, 1):
            raise ValueError(f"Invalid boolean value: {value}")
        return bool(value)

    def pointer(self) -> int:
        """Read a pointer token (currently 16-bit in this data set)."""
        return self.u16()

    def utf16_be(self, n: int = 1) -> str:
        """Read UTF-16 big-endian text with a code-unit count."""
        return self.stream.read(n * 2).decode("utf-16-be")

    def interval(self) -> tuple[float, float]:
        """Read a pair of float64 values."""
        return (self.f64(), self.f64())

    def vector(self) -> tuple[float, float, float]:
        """Read a 3D vector encoded as three float64 values."""
        return (self.f64(), self.f64(), self.f64())

    def box(
        self,
    ) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
        """Read a Parasolid box value (three intervals)."""
        return (self.interval(), self.interval(), self.interval())

    def str_u8_len(self) -> str:
        """Read ASCII text prefixed by a u8 byte length."""
        return self.stream.read(self.u8()).decode("ascii")

    def str_u32_len(self) -> str:
        """Read ASCII text prefixed by a u32 byte length."""
        return self.stream.read(self.u32()).decode("ascii")


class FieldType(Enum):
    """Schema scalar and composite field codes."""

    U8 = "u"
    CHAR = "c"
    LOGICAL = "l"
    U16 = "n"
    UTF16 = "w"
    U32 = "d"
    POINTER = "p"
    F64 = "f"
    INTERVAL = "i"
    VECTOR = "v"
    BOX = "b"
    H = "h"


READERS: dict[FieldType, Callable[[Reader], object]] = {
    FieldType.U8: lambda r: r.u8(),
    FieldType.CHAR: lambda r: r.char(),
    FieldType.LOGICAL: lambda r: r.bool8(),
    FieldType.U16: lambda r: r.u16(),
    FieldType.UTF16: lambda r: r.utf16_be(),
    FieldType.U32: lambda r: r.u32(),
    FieldType.POINTER: lambda r: r.pointer(),
    FieldType.F64: lambda r: r.f64(),
    FieldType.INTERVAL: lambda r: r.interval(),
    FieldType.VECTOR: lambda r: r.vector(),
    FieldType.BOX: lambda r: r.box(),
    FieldType.H: lambda r: r.vector(),
}
