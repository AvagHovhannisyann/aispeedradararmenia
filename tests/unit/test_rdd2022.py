"""Tests for RDD2022 ingestion.

No network: the annotation parser and the splitter are pure functions, and they are
where the correctness risk lives. The parser fixtures are copied from real RDD2022
files fetched during development, not invented.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ml" / "datasets"))

from rdd2022 import (  # noqa: E402
    CLASS_MAP,
    AnnotationError,
    ImageAnnotation,
    contiguous_splits,
    parse_voc,
)

# Verbatim shape of a real Czech annotation.
REAL_ANNOTATION = b"""<annotation>
  <folder>images</folder>
  <filename>Czech_000006.jpg</filename>
  <size>
    <depth>3</depth>
    <width>600</width>
    <height>600</height>
  </size>
  <object>
    <name>D00</name>
    <bndbox>
      <xmin>141</xmin>
      <ymin>405</ymin>
      <xmax>166</xmax>
      <ymax>472</ymax>
    </bndbox>
  </object>
</annotation>"""

# Real RDD2022 images frequently have no damage at all.
NEGATIVE_ANNOTATION = b"""<annotation>
  <folder>images</folder>
  <filename>Czech_000000.jpg</filename>
  <size><depth>3</depth><width>600</width><height>600</height></size>
</annotation>"""


class TestParsing:
    def test_parses_a_real_annotation(self):
        ann = parse_voc(REAL_ANNOTATION, image_id="Czech_000006")
        assert ann.image_id == "Czech_000006"
        assert ann.width == 600 and ann.height == 600
        assert ann.country == "Czech"
        assert ann.index == 6
        assert len(ann.boxes) == 1

        box = ann.boxes[0]
        assert box.rdd_code == "D00"
        assert box.damage_class == "longitudinal_crack"
        assert (box.xmin, box.ymin, box.xmax, box.ymax) == (141, 405, 166, 472)

    def test_negative_image_is_valid_not_an_error(self):
        """Images with no damage are a large share of RDD2022 and are kept — a model
        trained only on damaged road learns that road always has damage."""
        ann = parse_voc(NEGATIVE_ANNOTATION, image_id="Czech_000000")
        assert ann.boxes == []
        assert ann.is_negative

    def test_all_four_classes_map(self):
        for code, expected in CLASS_MAP.items():
            xml = REAL_ANNOTATION.replace(b"<name>D00</name>", f"<name>{code}</name>".encode())
            ann = parse_voc(xml, image_id="Czech_000006")
            assert ann.boxes[0].damage_class == expected

    def test_unknown_codes_are_dropped_not_guessed(self):
        """RDD2022 contains sporadic codes outside the four benchmark classes. Folding
        them into a neighbouring class would corrupt the ontology silently."""
        xml = REAL_ANNOTATION.replace(b"<name>D00</name>", b"<name>D43</name>")
        assert parse_voc(xml, image_id="Czech_000006").boxes == []

    def test_malformed_xml_raises(self):
        with pytest.raises(AnnotationError, match="malformed XML"):
            parse_voc(b"<annotation><unclosed>", image_id="x_000001")

    def test_missing_size_raises(self):
        with pytest.raises(AnnotationError, match="size"):
            parse_voc(b"<annotation><filename>a.jpg</filename></annotation>", image_id="x_000001")

    def test_zero_dimensions_raise(self):
        xml = b"<annotation><size><width>0</width><height>0</height></size></annotation>"
        with pytest.raises(AnnotationError):
            parse_voc(xml, image_id="x_000001")

    def test_degenerate_box_is_dropped(self):
        """A zero-area box makes torchvision's loss NaN deep inside training, with an
        error that gives no hint where it came from."""
        xml = REAL_ANNOTATION.replace(b"<xmax>166</xmax>", b"<xmax>141</xmax>")
        assert parse_voc(xml, image_id="Czech_000006").boxes == []

    def test_box_is_clamped_to_the_image(self):
        xml = REAL_ANNOTATION.replace(b"<xmax>166</xmax>", b"<xmax>9999</xmax>")
        box = parse_voc(xml, image_id="Czech_000006").boxes[0]
        assert box.xmax == 600

    def test_negative_coordinates_clamped(self):
        xml = REAL_ANNOTATION.replace(b"<xmin>141</xmin>", b"<xmin>-40</xmin>")
        assert parse_voc(xml, image_id="Czech_000006").boxes[0].xmin == 0

    def test_non_numeric_coordinate_is_dropped(self):
        xml = REAL_ANNOTATION.replace(b"<ymin>405</ymin>", b"<ymin>abc</ymin>")
        assert parse_voc(xml, image_id="Czech_000006").boxes == []

    def test_unparseable_id_does_not_crash(self):
        ann = parse_voc(REAL_ANNOTATION, image_id="weird-name")
        assert ann.country == "unknown"
        assert ann.index == 0


def make(image_id: str, country: str, index: int) -> ImageAnnotation:
    return ImageAnnotation(
        image_id=image_id,
        file_name=f"{image_id}.jpg",
        width=600,
        height=600,
        country=country,
        index=index,
    )


class TestSplitting:
    @pytest.fixture
    def annotations(self) -> list[ImageAnnotation]:
        return [make(f"Czech_{i:06d}", "Czech", i) for i in range(100)]

    def test_every_image_lands_in_exactly_one_split(self, annotations):
        splits = contiguous_splits(annotations)
        allocated = splits["train"] + splits["val"] + splits["test"]
        assert len(allocated) == 100
        assert len(set(allocated)) == 100

    def test_default_proportions(self, annotations):
        splits = contiguous_splits(annotations)
        assert len(splits["train"]) == 70
        assert len(splits["val"]) == 15
        assert len(splits["test"]) == 15

    def test_splits_are_contiguous_blocks(self, annotations):
        """The leakage guard (ADR-008).

        RDD2022 images are numbered along the drive, so consecutive numbers are metres
        apart and may show the same crack. A random split would scatter near-duplicates
        across train and test and inflate every metric. Contiguous blocks keep
        neighbours together, so the index ranges must not interleave.
        """
        splits = contiguous_splits(annotations)
        train = sorted(int(i.split("_")[1]) for i in splits["train"])
        test = sorted(int(i.split("_")[1]) for i in splits["test"])
        assert max(train) < min(test), "train and test index ranges overlap — leakage"

    def test_countries_split_independently(self):
        """Each country is blocked separately, so one country cannot end up entirely
        in test while another is entirely in train."""
        anns = [make(f"Czech_{i:06d}", "Czech", i) for i in range(50)]
        anns += [make(f"Japan_{i:06d}", "Japan", i) for i in range(50)]
        splits = contiguous_splits(anns)
        for name in ("train", "val", "test"):
            countries = {i.split("_")[0] for i in splits[name]}
            assert countries == {"Czech", "Japan"}

    def test_deterministic(self, annotations):
        assert contiguous_splits(annotations) == contiguous_splits(list(reversed(annotations)))

    def test_custom_proportions(self, annotations):
        splits = contiguous_splits(annotations, train=0.5, val=0.25)
        assert len(splits["train"]) == 50
        assert len(splits["val"]) == 25
        assert len(splits["test"]) == 25

    @pytest.mark.parametrize("train,val", [(0.0, 0.2), (1.0, 0.0), (0.8, 0.3), (-0.1, 0.2)])
    def test_invalid_proportions_rejected(self, annotations, train, val):
        with pytest.raises(ValueError):
            contiguous_splits(annotations, train=train, val=val)

    def test_empty_input(self):
        assert contiguous_splits([]) == {"train": [], "val": [], "test": []}
