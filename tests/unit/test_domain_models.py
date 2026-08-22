"""Domain model tests.

These lock in the invariants that keep RoadEye honest — chiefly that a coordinate
cannot exist without a stated method and uncertainty, and that a severity cannot exist
without a stated source.
"""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from roadeye.domain.enums import (
    RDD_CODE_TO_DAMAGE_CLASS,
    DamageClass,
    DefectStatus,
    LocationMethod,
    Severity,
    SeveritySource,
)
from roadeye.domain.models import BoundingBox, Defect, GeoPoint, ModelVersion

NOW = dt.datetime(2026, 8, 18, tzinfo=dt.UTC)


def geo(**kw) -> GeoPoint:
    return GeoPoint(
        lat=kw.pop("lat", 40.18231),
        lon=kw.pop("lon", 44.51491),
        method=kw.pop("method", LocationMethod.INTERPOLATED_PHONE_GPS),
        uncertainty_m=kw.pop("uncertainty_m", 8.0),
        **kw,
    )


def defect(**kw) -> Defect:
    params = {
        "defect_id": "d1",
        "damage_class": DamageClass.POTHOLE,
        "location": geo(),
        "confidence": 0.9,
        "first_seen": NOW,
        "last_seen": NOW,
    }
    params.update(kw)
    return Defect(**params)


class TestGeoPoint:
    def test_requires_method_and_uncertainty(self):
        """A bare lat/lon is a bug: it invites a reader to treat six decimal places as
        centimetre accuracy when the fix was a 12 m consumer GPS estimate."""
        with pytest.raises(ValidationError):
            GeoPoint(lat=40.0, lon=44.0)  # type: ignore[call-arg]

    @pytest.mark.parametrize("lat,lon", [(91.0, 44.0), (-91.0, 44.0), (40.0, 181.0)])
    def test_rejects_out_of_range(self, lat, lon):
        with pytest.raises(ValidationError):
            geo(lat=lat, lon=lon)

    def test_rejects_negative_uncertainty(self):
        with pytest.raises(ValidationError):
            geo(uncertainty_m=-1.0)


class TestBoundingBox:
    def test_rejects_inverted(self):
        with pytest.raises(ValidationError, match="degenerate"):
            BoundingBox(x1=100, y1=100, x2=50, y2=150)

    def test_rejects_zero_area(self):
        with pytest.raises(ValidationError, match="degenerate"):
            BoundingBox(x1=10, y1=10, x2=10, y2=20)

    def test_geometry(self):
        box = BoundingBox(x1=10, y1=20, x2=110, y2=220)
        assert box.width == 100
        assert box.height == 200
        assert box.area == 20_000


class TestDefectInvariants:
    def test_severity_must_declare_a_source(self):
        """An assessed severity with no stated source is exactly the false authority
        this system refuses to hand a government."""
        with pytest.raises(ValidationError, match="severity_source"):
            defect(severity=Severity.HIGH, severity_source=SeveritySource.OTHER)

    def test_human_assessed_severity_is_accepted(self):
        d = defect(severity=Severity.HIGH, severity_source=SeveritySource.HUMAN)
        assert d.severity is Severity.HIGH

    def test_unassessed_needs_no_source(self):
        assert defect().severity is Severity.UNASSESSED

    def test_rejects_reversed_timestamps(self):
        with pytest.raises(ValidationError, match="last_seen precedes"):
            defect(first_seen=NOW, last_seen=NOW - dt.timedelta(days=1))

    def test_defaults_to_probable(self):
        """Only a human may promote a defect beyond PROBABLE."""
        assert defect().status is DefectStatus.PROBABLE

    def test_confidence_bounded(self):
        with pytest.raises(ValidationError):
            defect(confidence=1.5)

    def test_unknown_fields_are_rejected(self):
        """Silently dropping an unexpected key turns a collector-side rename into a
        week-long debugging session."""
        with pytest.raises(ValidationError):
            defect(hpothesis="typo")


class TestModelVersionProvenance:
    def test_distribution_requires_stated_licenses(self):
        """BLOCKING-1: a model whose lineage includes RDD2022 may not be
        distributable, and that fact must travel with the weights."""
        with pytest.raises(ValidationError, match="training_data_licenses"):
            ModelVersion(
                model_id="m1",
                name="test",
                architecture="rtmdet",
                framework="mmdet",
                distribution_allowed=True,
            )

    def test_distribution_allowed_with_licenses(self):
        model = ModelVersion(
            model_id="m1",
            name="test",
            architecture="rtmdet",
            framework="mmdet",
            training_data_licenses=["proprietary-armenian-v1"],
            distribution_allowed=True,
        )
        assert model.distribution_allowed

    def test_defaults_to_not_distributable(self):
        """Fail closed: an unaudited model is not shippable until proven otherwise."""
        model = ModelVersion(model_id="m1", name="t", architecture="a", framework="f")
        assert model.distribution_allowed is False


class TestOntology:
    def test_rdd_codes_map_to_all_four_classes(self):
        assert set(RDD_CODE_TO_DAMAGE_CLASS) == {"D00", "D10", "D20", "D40"}
        assert set(RDD_CODE_TO_DAMAGE_CLASS.values()) == set(DamageClass)

    def test_pothole_is_d40(self):
        assert RDD_CODE_TO_DAMAGE_CLASS["D40"] is DamageClass.POTHOLE

    def test_enum_values_are_stable_strings(self):
        """These values are persisted and exported; renaming one is a breaking change."""
        assert DamageClass.POTHOLE.value == "pothole"
        assert DamageClass.ALLIGATOR_CRACK.value == "alligator_crack"
        assert DefectStatus.PROBABLE.value == "probable"
