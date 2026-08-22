"""RDD2022 ingestion with provenance.

RDD2022 is the bootstrap dataset: 47,420 road images from six countries with >55,000
annotated damage instances. It gets RoadEye a detector before any Armenian data exists.

Three practical facts, each discovered by trying:

1. **The per-country download links in the authors' README are dead.** The S3 bucket
   (``bigdatacup.s3.ap-northeast-1.amazonaws.com``) returns ``AccessDenied``. Only the
   Figshare DOI record still serves the data.
2. **Figshare serves one 13.3 GB archive**, which is more than many development
   machines can spare once extracted — and it is a ZIP of ZIPs, one per country,
   stored uncompressed. So :mod:`remote_zip` reads countries out of it selectively.
3. **Norway is 10.6 GB of that 13.3 GB.** Skipping it saves 80% of the download. The
   other six countries together are ~2.6 GB.

**Licence warning.** RDD2022 is published under two contradictory licences: the
Figshare record says CC BY 4.0, the authors' repository says CC BY-SA 4.0. Under the
share-alike reading, distributing a model trained on it could oblige us to publish that
model. Every dataset produced here is therefore stamped ``distribution_allowed: false``
and models trained from it are quarantined. See ``docs/LICENSE_AUDIT.md`` BLOCKING-1.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from remote_zip import RemoteZip

#: Figshare download for DOI 10.6084/m9.figshare.21431547.
RDD2022_URL = "https://ndownloader.figshare.com/files/38030910"
RDD2022_DOI = "10.6084/m9.figshare.21431547.v1"

#: Approximate uncompressed size of each nested country archive, measured 2026-08-22.
#: Norway is deliberately listed last: at 10.6 GB it dwarfs everything else, and most
#: users should not fetch it.
COUNTRY_SIZES_GB = {
    "China_Drone": 0.16,
    "China_MotorBike": 0.19,
    "Czech": 0.26,
    "United_States": 0.44,
    "India": 0.53,
    "Japan": 1.07,
    "Norway": 10.61,
}

#: RDD damage codes -> RoadEye class names. Mirrors
#: :data:`roadeye.domain.enums.RDD_CODE_TO_DAMAGE_CLASS`; the original code is kept on
#: every annotation so provenance back to the source label is exact.
CLASS_MAP = {
    "D00": "longitudinal_crack",
    "D10": "transverse_crack",
    "D20": "alligator_crack",
    "D40": "pothole",
}

LICENSE_NOTE = (
    "DISPUTED: Figshare record states CC BY 4.0; the authors' repository "
    "(github.com/sekilab/RoadDamageDetector) states CC BY-SA 4.0. Treated as the "
    "stricter CC BY-SA 4.0 until clarified. Models derived from this data are NOT "
    "distributable. See docs/LICENSE_AUDIT.md BLOCKING-1."
)

CITATION = (
    "Arya, Deeksha; Maeda, Hiroya; Sekimoto, Yoshihide; Omata, Hiroshi; Ghosh, "
    "Sanjay Kumar; Toshniwal, Durga; et al. (2022). RDD2022 - The multi-national "
    "Road Damage Dataset released through CRDDC'2022. figshare. Dataset. "
    "https://doi.org/10.6084/m9.figshare.21431547.v1"
)

#: Image ids look like "Czech_000006"; the number orders images along a drive.
_ID_PATTERN = re.compile(r"^(?P<country>.+)_(?P<index>\d+)$")


@dataclass(frozen=True, slots=True)
class BoxAnnotation:
    """One annotated damage instance, in absolute pixels."""

    rdd_code: str
    damage_class: str
    xmin: int
    ymin: int
    xmax: int
    ymax: int


@dataclass
class ImageAnnotation:
    """Everything known about one annotated image."""

    image_id: str
    file_name: str
    width: int
    height: int
    country: str
    #: Sequence number within the country, used for leakage-safe splitting.
    index: int
    boxes: list[BoxAnnotation] = field(default_factory=list)

    @property
    def is_negative(self) -> bool:
        """True when the image has no annotated damage.

        These matter: a dataset of only damaged road teaches a model that road always
        contains damage. RDD2022 contains many such images and they are kept.
        """
        return not self.boxes


class AnnotationError(ValueError):
    """A VOC annotation could not be parsed."""


def parse_voc(xml_bytes: bytes, *, image_id: str) -> ImageAnnotation:
    """Parse one RDD2022 Pascal-VOC annotation.

    Malformed boxes are dropped rather than raising: a single bad annotation in 47,000
    should not stop a dataset build, and a silently *wrong* box is worse than a missing
    one. Whether the file itself is unparseable is a different matter, and does raise.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise AnnotationError(f"{image_id}: malformed XML ({exc})") from exc

    size = root.find("size")
    if size is None:
        raise AnnotationError(f"{image_id}: no <size> element")

    def _int(node: ET.Element | None, tag: str) -> int | None:
        if node is None:
            return None
        child = node.find(tag)
        if child is None or child.text is None:
            return None
        try:
            return int(float(child.text.strip()))
        except ValueError:
            return None

    width = _int(size, "width")
    height = _int(size, "height")
    if not width or not height:
        raise AnnotationError(f"{image_id}: missing or zero image dimensions")

    file_name = (root.findtext("filename") or f"{image_id}.jpg").strip()
    match = _ID_PATTERN.match(image_id)
    country = match.group("country") if match else "unknown"
    index = int(match.group("index")) if match else 0

    boxes: list[BoxAnnotation] = []
    for obj in root.findall("object"):
        code = (obj.findtext("name") or "").strip()
        if code not in CLASS_MAP:
            # RDD2022 contains sporadic codes outside the four benchmark classes
            # (D01, D11, D43, D44...). Ignoring them keeps the ontology honest rather
            # than silently folding them into a class they do not belong to.
            continue
        bnd = obj.find("bndbox")
        xmin, ymin = _int(bnd, "xmin"), _int(bnd, "ymin")
        xmax, ymax = _int(bnd, "xmax"), _int(bnd, "ymax")
        if None in (xmin, ymin, xmax, ymax):
            continue
        # Clamp to the image and reject degenerate boxes; a zero-area box crashes
        # torchvision's loss computation with an unhelpful error deep in training.
        xmin, ymin = max(0, xmin), max(0, ymin)
        xmax, ymax = min(width, xmax), min(height, ymax)
        if xmax <= xmin or ymax <= ymin:
            continue
        boxes.append(
            BoxAnnotation(
                rdd_code=code,
                damage_class=CLASS_MAP[code],
                xmin=xmin,
                ymin=ymin,
                xmax=xmax,
                ymax=ymax,
            )
        )

    return ImageAnnotation(
        image_id=image_id,
        file_name=file_name,
        width=width,
        height=height,
        country=country,
        index=index,
        boxes=boxes,
    )


def contiguous_splits(
    annotations: list[ImageAnnotation],
    *,
    train: float = 0.7,
    val: float = 0.15,
) -> dict[str, list[str]]:
    """Split by contiguous index blocks per country, never randomly.

    RDD2022 images are numbered in the order they were captured along a drive, so
    ``Czech_000101`` and ``Czech_000102`` are very likely metres apart and may show the
    same crack. A random split would put near-duplicate images either side of the
    train/test boundary and inflate every metric — the exact leakage ADR-008 forbids.

    Splitting into contiguous blocks keeps neighbouring frames together. It is a
    weaker guarantee than the route-disjoint split we will use for Armenian data (where
    we know the actual routes), and it is the best available here.
    """
    if not 0 < train < 1 or not 0 <= val < 1 or train + val >= 1:
        raise ValueError(f"invalid split fractions: train={train}, val={val}")

    splits: dict[str, list[str]] = {"train": [], "val": [], "test": []}
    by_country: dict[str, list[ImageAnnotation]] = {}
    for ann in annotations:
        by_country.setdefault(ann.country, []).append(ann)

    for country_anns in by_country.values():
        ordered = sorted(country_anns, key=lambda a: a.index)
        n = len(ordered)
        train_end = int(n * train)
        val_end = int(n * (train + val))
        for i, ann in enumerate(ordered):
            bucket = "train" if i < train_end else ("val" if i < val_end else "test")
            splits[bucket].append(ann.image_id)
    return splits


def fetch_country(
    country: str,
    dest: str | Path,
    *,
    limit: int | None = None,
    url: str = RDD2022_URL,
    include_negatives: bool = True,
    progress: bool = True,
) -> DatasetBuild:
    """Download one country's annotated training images and build a dataset directory.

    Only the requested country is transferred, using HTTP range requests into the
    nested archive. ``limit`` caps the number of images, which is what makes a quick
    smoke test possible without pulling hundreds of MB.
    """
    if country not in COUNTRY_SIZES_GB:
        raise ValueError(f"unknown country {country!r}; expected one of {sorted(COUNTRY_SIZES_GB)}")

    dest = Path(dest)
    images_dir = dest / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    started = time.time()
    outer = RemoteZip(url)
    inner = outer.open_nested(f"RDD2022/{country}.zip")

    # Only the train split carries annotations; the official test split is unlabelled
    # (it was the competition's hidden set), so it is useless for our purposes.
    xml_entries = sorted(
        inner.iter_matching(prefix=f"{country}/train/annotations", suffix=".xml"),
        key=lambda e: e.name,
    )
    image_index = {
        Path(e.name).stem: e
        for e in inner.iter_matching(prefix=f"{country}/train/images", suffix=".jpg")
    }

    if limit is not None:
        xml_entries = xml_entries[:limit]

    annotations: list[ImageAnnotation] = []
    errors: list[str] = []
    bytes_fetched = 0

    # Annotations first, in bulk: they are tiny and tell us which images are worth
    # fetching. Pulling every image and then discarding the negatives would waste most
    # of the transfer when include_negatives is off.
    if progress:
        print(f"  reading {len(xml_entries)} annotations…", flush=True)
    parsed: list[ImageAnnotation] = []
    for entry, blob in inner.read_many(xml_entries):
        image_id = Path(entry.name).stem
        try:
            ann = parse_voc(blob, image_id=image_id)
        except AnnotationError as exc:
            errors.append(str(exc))
            continue
        if not include_negatives and ann.is_negative:
            continue
        if image_id not in image_index:
            errors.append(f"{image_id}: annotation has no matching image")
            continue
        parsed.append(ann)
        bytes_fetched += len(blob)

    wanted = [image_index[a.image_id] for a in parsed]
    by_id = {a.image_id: a for a in parsed}
    if progress:
        print(f"  fetching {len(wanted)} images…", flush=True)

    for entry, blob in inner.read_many(wanted):
        image_id = Path(entry.name).stem
        (images_dir / f"{image_id}.jpg").write_bytes(blob)
        bytes_fetched += len(blob)
        annotations.append(by_id[image_id])
        if progress and len(annotations) % 100 == 0:
            print(
                f"    {len(annotations)}/{len(wanted)} "
                f"({bytes_fetched / 1e6:.0f} MB, {time.time() - started:.0f}s)",
                flush=True,
            )

    build = DatasetBuild(
        name=f"rdd2022_{country.lower()}",
        country=country,
        annotations=annotations,
        errors=errors,
        bytes_fetched=bytes_fetched,
        source_url=url,
        elapsed_s=round(time.time() - started, 1),
    )
    build.write(dest)
    return build


@dataclass
class DatasetBuild:
    """The result of an ingestion run, plus the provenance record it writes."""

    name: str
    country: str
    annotations: list[ImageAnnotation]
    errors: list[str]
    bytes_fetched: int
    source_url: str
    elapsed_s: float

    @property
    def box_count(self) -> int:
        return sum(len(a.boxes) for a in self.annotations)

    def class_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for ann in self.annotations:
            for box in ann.boxes:
                counts[box.damage_class] = counts.get(box.damage_class, 0) + 1
        return counts

    def write(self, dest: str | Path) -> Path:
        """Write annotations, splits and a provenance manifest."""
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)

        with (dest / "annotations.jsonl").open("w", encoding="utf-8") as fh:
            for ann in self.annotations:
                record = asdict(ann)
                record["boxes"] = [asdict(b) for b in ann.boxes]
                fh.write(json.dumps(record) + "\n")

        splits = contiguous_splits(self.annotations)
        (dest / "splits.json").write_text(json.dumps(splits, indent=2), encoding="utf-8")

        manifest = {
            "schema_version": 1,
            "name": self.name,
            "source": "RDD2022",
            "source_url": self.source_url,
            "doi": RDD2022_DOI,
            "country": self.country,
            "retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "license": "CC BY-SA 4.0 (assumed; see license_notes)",
            "license_notes": LICENSE_NOTE,
            "distribution_allowed": False,
            "citation": CITATION,
            "class_map": CLASS_MAP,
            "image_count": len(self.annotations),
            "annotation_count": self.box_count,
            "negative_image_count": sum(1 for a in self.annotations if a.is_negative),
            "class_counts": self.class_counts(),
            "split_strategy": "contiguous index blocks per country (see ADR-008)",
            "split_sizes": {k: len(v) for k, v in splits.items()},
            "bytes_fetched": self.bytes_fetched,
            "elapsed_s": self.elapsed_s,
            "errors": self.errors[:50],
            "error_count": len(self.errors),
            "content_hash": self.content_hash(),
        }
        (dest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return dest

    def content_hash(self) -> str:
        """Stable hash of the annotation content, so a dataset version is verifiable."""
        digest = hashlib.blake2b(digest_size=16)
        for ann in sorted(self.annotations, key=lambda a: a.image_id):
            digest.update(ann.image_id.encode())
            for box in ann.boxes:
                digest.update(f"{box.rdd_code}{box.xmin},{box.ymin},{box.xmax},{box.ymax}".encode())
        return digest.hexdigest()

    def summary(self) -> str:
        return (
            f"{self.name}: {len(self.annotations)} images, {self.box_count} boxes, "
            f"{sum(1 for a in self.annotations if a.is_negative)} negatives, "
            f"{self.bytes_fetched / 1e6:.0f} MB, {len(self.errors)} errors"
        )


def load_annotations(dataset_dir: str | Path) -> Iterator[ImageAnnotation]:
    """Read back a written dataset."""
    path = Path(dataset_dir) / "annotations.jsonl"
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            raw = json.loads(line)
            boxes = [BoxAnnotation(**b) for b in raw.pop("boxes", [])]
            yield ImageAnnotation(**raw, boxes=boxes)
