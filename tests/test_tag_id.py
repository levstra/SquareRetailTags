import pytest
from fastapi import HTTPException

from esl.web.routers.tags import normalize_tag_id as _normalize_tag_id


def test_uppercases_lowercase_hex():
    assert _normalize_tag_id("a1b2c3d4e5f6") == "A1B2C3D4E5F6"


def test_passes_through_already_uppercase():
    assert _normalize_tag_id("840001282A7E") == "840001282A7E"


def test_accepts_mixed_case():
    assert _normalize_tag_id("aAbB11223344") == "AABB11223344"


@pytest.mark.parametrize(
    "bad",
    [
        "nonsense",          # not hex at all
        "A1B2C3D4E5F",       # 11 chars — too short
        "A1B2C3D4E5F6A",     # 13 chars — too long
        "A1B2C3D4E5G6",      # G is not hex
        "",                  # empty
        "A1B2-C3D4-E5F6",    # separators
        "A1B2 C3D4E5F6",     # inner whitespace
    ],
)
def test_rejects_anything_that_is_not_12_hex(bad):
    with pytest.raises(HTTPException) as exc:
        _normalize_tag_id(bad)
    assert exc.value.status_code == 422  # the API spec's validation status


def test_rejects_the_16_char_length_this_repo_used_before():
    """The pre-hardware assumption was 16 chars; the real SERTAG id is 12."""
    with pytest.raises(HTTPException):
        _normalize_tag_id("A1B2C3D4E5F6A1B2")


def test_trims_surrounding_whitespace():
    """Scanners and pasted IDs often carry stray whitespace; the shared rule trims it."""
    assert _normalize_tag_id(" a1b2c3d4e5f6\t") == "A1B2C3D4E5F6"
