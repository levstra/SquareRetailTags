"""What a scanned barcode is: a shelf tag or a product (spec §5.2)."""
import re
from typing import Literal

ScanKind = Literal["tag", "product"]

_TAG_ID = re.compile(r"[0-9A-F]{12}")
_HEX_LETTER = re.compile(r"[A-F]")
_DIGITS = re.compile(r"[0-9]+")


class EmptyScan(ValueError):
    """The scanner produced nothing but whitespace."""


def normalize_tag_id(code: str) -> str | None:
    """The 12-hex tag ID this scan encodes, or None if it can't be one.

    Known formats (the only place they are listed):
    - On-screen barcode of an unassigned tag: Code 128 of exactly the 12-hex
      tagId (Pi contract §4.1).
    - Bottom sticker: not yet recorded (Task 0 deferred); no rule yet.
    """
    value = code.strip().upper()
    return value if _TAG_ID.fullmatch(value) else None


def classify(code: str, *, known_tag_ids: set[str]) -> ScanKind:
    if not code.strip():
        raise EmptyScan()
    tag_id = normalize_tag_id(code)
    if tag_id is None:
        return "product"
    if _HEX_LETTER.search(tag_id):
        return "tag"
    # 12 digits: also a valid UPC-A. Only a tag if this business already knows it.
    return "tag" if tag_id in known_tag_ids else "product"


def gtin_candidates(code: str) -> list[str]:
    """Equivalent spellings of a GTIN, so a UPC-A matches its EAN-13/GTIN-14 forms."""
    value = code.strip()
    if not _DIGITS.fullmatch(value):
        return []
    core = value.lstrip("0") or "0"
    spellings = {value}
    for length in (8, 12, 13, 14):
        if len(core) <= length:
            spellings.add(core.zfill(length))
    return sorted(spellings)
