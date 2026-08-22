"""Attribution must follow the data, not a caller's memory.

OpenStreetMap is ODbL: an export whose positions were map-matched against OSM geometry
carries an attribution obligation, and that obligation outlives the session that produced
the file. So it is derived from the defects rather than passed in.

Both directions matter. An export that never touched OSM must *not* claim to have used
it: an attribution added unconditionally is noise, and a field that is always present is
a field readers learn to skip — which is exactly when it stops functioning as compliance.
"""

from __future__ import annotations

import datetime as dt
import json

from roadeye.domain.enums import DamageClass, DefectStatus, LocationMethod
from roadeye.domain.models import Defect, GeoPoint, RoadSegmentRef
from roadeye.reporting.export import required_attribution, to_csv, to_geojson

NOW = dt.datetime(2026, 8, 22, 9, 0, tzinfo=dt.UTC)


def make_defect(defect_id: str, *, road: RoadSegmentRef | None = None) -> Defect:
    return Defect(
        defect_id=defect_id,
        damage_class=DamageClass.POTHOLE,
        location=GeoPoint(
            lat=40.1850,
            lon=44.5150,
            method=(
                LocationMethod.ROAD_SEGMENT_MATCHED
                if road
                else LocationMethod.INTERPOLATED_PHONE_GPS
            ),
            uncertainty_m=8.0,
        ),
        road=road,
        confidence=0.7,
        status=DefectStatus.PROBABLE,
        first_seen=NOW,
        last_seen=NOW,
        survey_ids=["s1"],
        observation_count=2,
    )


OSM_ROAD = RoadSegmentRef(source="osm", segment_id="way/1#0", name="Mashtots Avenue")
SYNTHETIC_ROAD = RoadSegmentRef(source="synthetic", segment_id="way/9#0", name="Demo St")


class TestRequiredAttribution:
    def test_osm_matched_defects_oblige_attribution(self):
        notice = required_attribution([make_defect("d1", road=OSM_ROAD)])
        assert notice is not None
        assert "OpenStreetMap" in notice and "ODbL" in notice

    def test_unmatched_defects_oblige_nothing(self):
        assert required_attribution([make_defect("d1")]) is None

    def test_a_non_osm_source_obliges_nothing(self):
        """The demo network is invented geometry. Attributing OSM for it would be a
        false statement about provenance, in a field whose whole job is provenance."""
        assert required_attribution([make_defect("d1", road=SYNTHETIC_ROAD)]) is None

    def test_one_matched_defect_in_a_batch_is_enough(self):
        defects = [make_defect("d1"), make_defect("d2", road=OSM_ROAD)]
        assert required_attribution(defects) is not None


class TestGeoJson:
    def test_attribution_lands_in_the_file(self, tmp_path):
        collection = to_geojson([make_defect("d1", road=OSM_ROAD)], tmp_path / "d.geojson")
        assert "OpenStreetMap" in collection["attribution"]

        written = json.loads((tmp_path / "d.geojson").read_text(encoding="utf-8"))
        assert written["attribution"] == collection["attribution"]

    def test_no_attribution_key_when_none_is_owed(self, tmp_path):
        collection = to_geojson([make_defect("d1")], tmp_path / "d.geojson")
        assert "attribution" not in collection

    def test_an_explicit_attribution_still_wins(self, tmp_path):
        collection = to_geojson([make_defect("d1")], tmp_path / "d.geojson", attribution="Mine")
        assert collection["attribution"] == "Mine"


class TestCsvSidecar:
    """CSV has nowhere to put a licence notice — no header, no metadata block — and a
    spreadsheet emailed to a municipality travels alone."""

    def test_a_sidecar_is_written_when_attribution_is_owed(self, tmp_path):
        path = to_csv([make_defect("d1", road=OSM_ROAD)], tmp_path / "defects.csv")
        sidecar = path.with_suffix(path.suffix + ".ATTRIBUTION.txt")
        assert sidecar.exists()
        assert "OpenStreetMap" in sidecar.read_text(encoding="utf-8")

    def test_no_sidecar_when_nothing_is_owed(self, tmp_path):
        path = to_csv([make_defect("d1")], tmp_path / "defects.csv")
        assert not path.with_suffix(path.suffix + ".ATTRIBUTION.txt").exists()

    def test_a_stale_sidecar_is_removed_on_re_export(self, tmp_path):
        """Otherwise the previous run's notice sits next to data it no longer describes,
        which is worse than no notice: it is a false claim about where the data came
        from."""
        path = tmp_path / "defects.csv"
        to_csv([make_defect("d1", road=OSM_ROAD)], path)
        sidecar = path.with_suffix(path.suffix + ".ATTRIBUTION.txt")
        assert sidecar.exists()

        to_csv([make_defect("d1")], path)
        assert not sidecar.exists()
