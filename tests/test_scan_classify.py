import json
from pathlib import Path

import pytest

from esl.scan import EmptyScan, classify, gtin_candidates, normalize_tag_id

FIXTURE = Path(__file__).parent / "fixtures" / "scan" / "tag_barcodes.json"


@pytest.mark.parametrize("code, kind", [
    ("840001262D51", "tag"),          # 12 hex with letters
    (" 840001262d51\n", "tag"),       # whitespace + lower case
    ("ABCDEFABCDEF", "tag"),
    ("036000291452", "product"),      # UPC-A, unknown as a tag
    ("4006381333931", "product"),     # EAN-13
    ("96385074", "product"),          # EAN-8
    ("10012345678902", "product"),    # GTIN-14
    ("TC24-HBY", "product"),          # Code 128 SKU
    ("840001262D5", "product"),       # 11 chars: not a tag ID
    ("840001262D512", "product"),     # 13 chars: not a tag ID
    ("84000126ZD51", "product"),      # not hex
])
def test_classify_without_known_tags(code, kind):
    assert classify(code, known_tag_ids=set()) == kind


def test_all_digit_known_tag_is_a_tag():
    assert classify("840001262051", known_tag_ids={"840001262051"}) == "tag"


def test_all_digit_unknown_is_a_product():
    assert classify("840001262051", known_tag_ids={"840001262D51"}) == "product"


@pytest.mark.parametrize("code", ["", "   ", "\n"])
def test_empty_scan(code):
    with pytest.raises(EmptyScan):
        classify(code, known_tag_ids=set())


def test_normalize_tag_id():
    assert normalize_tag_id(" 840001262d51 ") == "840001262D51"
    assert normalize_tag_id("036000291452") == "036000291452"  # shape only; classify decides
    assert normalize_tag_id("TC24-HBY") is None


def test_gtin_candidates_bridge_upc_and_ean13():
    # Scanners report a UPC-A either as 12 digits or as EAN-13 with a leading 0.
    assert "036000291452" in gtin_candidates("0036000291452")
    assert "0036000291452" in gtin_candidates("036000291452")
    assert "00036000291452" in gtin_candidates("036000291452")  # GTIN-14 spelling
    assert gtin_candidates("TC24-HBY") == []


@pytest.mark.skipif(not FIXTURE.exists(), reason="Task 0 not done: no real tag scan recorded")
def test_real_tag_barcodes():
    sample = json.loads(FIXTURE.read_text())
    assert normalize_tag_id(sample["screen"]) == sample["tag_id"]
    assert classify(sample["screen"], known_tag_ids={sample["tag_id"]}) == "tag"
    if sample.get("sticker") and sample.get("sticker_is_tag_id", True):
        assert normalize_tag_id(sample["sticker"]) == sample["tag_id"]
