"""Human-readable data size parsing/formatting (MB/GB)."""

from __future__ import annotations

from app.core.validators import ValidationError

KB = 1024
MB = 1024 * 1024
GB = 1024 * 1024 * 1024
TB = 1024 * 1024 * 1024 * 1024

_UNITS = {
    "b": 1,
    "k": KB,
    "kb": KB,
    "kib": KB,
    "m": MB,
    "mb": MB,
    "mib": MB,
    "g": GB,
    "gb": GB,
    "gib": GB,
    "t": TB,
    "tb": TB,
    "tib": TB,
}


def parse_size(value: str | int | float | None) -> int | None:
    """Parse ``"500MB"``/``"2GB"``/``"1024"`` into a byte count.

    ``None``/empty/``"0"`` returns ``None`` (meaning unlimited).
    """

    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) or None
    text = value.strip().lower()
    if not text or text in {"0", "unlimited", "none"}:
        return None
    number = ""
    unit = ""
    for char in text:
        if char.isdigit() or char == ".":
            number += char
        else:
            unit += char
    if not number:
        raise ValidationError(f"invalid size: {value!r}")
    try:
        amount = float(number)
    except ValueError as exc:
        raise ValidationError(f"invalid size: {value!r}") from exc
    unit = unit.strip()
    if not unit:
        factor = 1
    else:
        factor = _UNITS.get(unit)
        if factor is None:
            raise ValidationError(f"unknown size unit: {unit!r}")
    return int(amount * factor) or None


def format_size(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "unlimited"
    num = float(num_bytes)
    for unit, factor in (("TB", TB), ("GB", GB), ("MB", MB), ("KB", KB)):
        if num >= factor:
            return f"{num / factor:.2f} {unit}"
    return f"{int(num)} B"
