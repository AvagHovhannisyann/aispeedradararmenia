"""SQLite storage with an R*Tree spatial index.

Zero-install, single-file, transactional — the right database for a one-laptop MVP
(ADR-003). The migration path to PostgreSQL/PostGIS is documented in
``docs/DATA_MODEL.md``; we deliberately do **not** build a generic ORM abstraction to
prepare for it, because a speculative abstraction costs more than the eventual port.

Two things are load-bearing here:

* **The R*Tree index.** SQLite's ``rtree`` virtual table answers "which defects are
  near this point" without a GIS server. It is an index over bounding boxes, so the
  standard pattern is index-then-refine: query the box, then filter by true distance.
* **Append-only review history.** ``reviews`` is never updated or deleted. A human
  correction is evidence, and a system that sells auditability to a government cannot
  quietly overwrite it.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roadeye.domain.enums import DamageClass, DefectStatus, LocationMethod, Severity, SeveritySource
from roadeye.domain.models import (
    Defect,
    DefectObservation,
    Detection,
    Frame,
    GeoPoint,
    ModelVersion,
    ProcessingRun,
    Review,
    Survey,
)
from roadeye.geolocation.geodesy import LatLon, bounding_box, haversine_m

#: Storage schema version, tracked in the ``meta`` table. Bump alongside a migration.
STORAGE_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS surveys (
    survey_id                 TEXT PRIMARY KEY,
    schema_version            INTEGER NOT NULL,
    started_at                TEXT NOT NULL,
    ended_at                  TEXT,
    recording_start_epoch_ms  INTEGER NOT NULL,
    video_path                TEXT,
    device_json               TEXT NOT NULL DEFAULT '{}',
    app_version               TEXT,
    notes                     TEXT,
    ingest_stats_json         TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS frames (
    frame_id            TEXT PRIMARY KEY,
    survey_id           TEXT NOT NULL REFERENCES surveys(survey_id) ON DELETE CASCADE,
    video_time_s        REAL NOT NULL,
    t_epoch_ms          INTEGER NOT NULL,
    width               INTEGER,
    height              INTEGER,
    image_path          TEXT,
    obs_lat             REAL,
    obs_lon             REAL,
    obs_method          TEXT,
    obs_uncertainty_m   REAL,
    speed_mps           REAL,
    heading_deg         REAL,
    quality             TEXT NOT NULL,
    quality_scores_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_frames_survey ON frames(survey_id, t_epoch_ms);

CREATE TABLE IF NOT EXISTS detections (
    detection_id  TEXT PRIMARY KEY,
    frame_id      TEXT NOT NULL REFERENCES frames(frame_id) ON DELETE CASCADE,
    survey_id     TEXT NOT NULL,
    damage_class  TEXT NOT NULL,
    confidence    REAL NOT NULL,
    x1 REAL NOT NULL, y1 REAL NOT NULL, x2 REAL NOT NULL, y2 REAL NOT NULL,
    mask_json     TEXT,
    model_id      TEXT NOT NULL,
    track_id      TEXT
);
CREATE INDEX IF NOT EXISTS idx_detections_frame ON detections(frame_id);
CREATE INDEX IF NOT EXISTS idx_detections_track ON detections(track_id);

CREATE TABLE IF NOT EXISTS defects (
    defect_id           TEXT PRIMARY KEY,
    schema_version      INTEGER NOT NULL,
    damage_class        TEXT NOT NULL,
    lat                 REAL NOT NULL,
    lon                 REAL NOT NULL,
    location_method     TEXT NOT NULL,
    uncertainty_m       REAL NOT NULL,
    road_source         TEXT,
    road_segment_id     TEXT,
    road_name           TEXT,
    confidence          REAL NOT NULL,
    severity            TEXT NOT NULL,
    severity_source     TEXT NOT NULL,
    status              TEXT NOT NULL,
    trend               TEXT NOT NULL,
    observation_count   INTEGER NOT NULL,
    survey_ids_json     TEXT NOT NULL DEFAULT '[]',
    first_seen          TEXT NOT NULL,
    last_seen           TEXT NOT NULL,
    representative_frame_id   TEXT,
    representative_image_path TEXT,
    model_id            TEXT,
    processing_run_id   TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_defects_status ON defects(status);
CREATE INDEX IF NOT EXISTS idx_defects_class ON defects(damage_class);

-- Spatial index. Bounding boxes are degenerate (point) rectangles; queries widen them
-- by the search radius. rowid is a stable integer alias for defect_id via defect_rowid.
CREATE VIRTUAL TABLE IF NOT EXISTS defects_rtree USING rtree(
    id, min_lat, max_lat, min_lon, max_lon
);

CREATE TABLE IF NOT EXISTS defect_rowid (
    rowid_int INTEGER PRIMARY KEY AUTOINCREMENT,
    defect_id TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS defect_observations (
    observation_id           TEXT PRIMARY KEY,
    defect_id                TEXT NOT NULL REFERENCES defects(defect_id) ON DELETE CASCADE,
    survey_id                TEXT NOT NULL,
    track_id                 TEXT,
    detection_ids_json       TEXT NOT NULL DEFAULT '[]',
    observed_at              TEXT NOT NULL,
    confidence               REAL NOT NULL,
    lat                      REAL NOT NULL,
    lon                      REAL NOT NULL,
    location_method          TEXT NOT NULL,
    uncertainty_m            REAL NOT NULL,
    representative_frame_id  TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_defect ON defect_observations(defect_id);
CREATE INDEX IF NOT EXISTS idx_obs_survey ON defect_observations(survey_id);

-- Append-only. No UPDATE, no DELETE. A human correction is evidence.
CREATE TABLE IF NOT EXISTS reviews (
    review_id       TEXT PRIMARY KEY,
    defect_id       TEXT NOT NULL REFERENCES defects(defect_id) ON DELETE CASCADE,
    action          TEXT NOT NULL,
    reviewer        TEXT NOT NULL,
    reviewed_at     TEXT NOT NULL,
    previous_value_json TEXT,
    new_value_json      TEXT,
    note            TEXT
);
CREATE INDEX IF NOT EXISTS idx_reviews_defect ON reviews(defect_id, reviewed_at);

CREATE TABLE IF NOT EXISTS model_versions (
    model_id                 TEXT PRIMARY KEY,
    name                     TEXT NOT NULL,
    architecture             TEXT NOT NULL,
    framework                TEXT NOT NULL,
    framework_version        TEXT,
    weights_path             TEXT,
    weights_origin           TEXT,
    weights_license          TEXT,
    dataset_id               TEXT,
    training_data_licenses_json TEXT NOT NULL DEFAULT '[]',
    distribution_allowed     INTEGER NOT NULL DEFAULT 0,
    classes_json             TEXT NOT NULL DEFAULT '[]',
    git_commit               TEXT,
    created_at               TEXT NOT NULL,
    metrics_json             TEXT NOT NULL DEFAULT '{}',
    notes                    TEXT
);

CREATE TABLE IF NOT EXISTS processing_runs (
    run_id                   TEXT PRIMARY KEY,
    survey_id                TEXT NOT NULL,
    started_at               TEXT NOT NULL,
    finished_at              TEXT,
    model_id                 TEXT,
    git_commit               TEXT,
    config_json              TEXT NOT NULL DEFAULT '{}',
    frames_decoded           INTEGER NOT NULL DEFAULT 0,
    frames_sampled           INTEGER NOT NULL DEFAULT 0,
    frames_rejected_quality  INTEGER NOT NULL DEFAULT 0,
    detections               INTEGER NOT NULL DEFAULT 0,
    tracks                   INTEGER NOT NULL DEFAULT 0,
    defects                  INTEGER NOT NULL DEFAULT 0,
    errors_json              TEXT NOT NULL DEFAULT '[]',
    warnings_json            TEXT NOT NULL DEFAULT '[]',
    duration_s               REAL
);
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Database:
    """A RoadEye SQLite database.

    Use as a context manager, or call :meth:`close`. Foreign keys are enabled
    explicitly because SQLite leaves them off by default — without that, cascading
    deletes silently do nothing and orphaned evidence accumulates.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        # WAL improves concurrent read/write behaviour for the local API + CLI running
        # at once. Not applicable to in-memory databases.
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a unit of work atomically."""
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ------------------------------------------------------------------ migration

    def _migrate(self) -> None:
        self._conn.executescript(_SCHEMA)
        cur = self._conn.execute("SELECT value FROM meta WHERE key = 'schema_version'")
        row = cur.fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(STORAGE_SCHEMA_VERSION),),
            )
            self._conn.commit()
        else:
            found = int(row["value"])
            if found > STORAGE_SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema version {found} is newer than this build "
                    f"supports ({STORAGE_SCHEMA_VERSION}); upgrade RoadEye"
                )

    # -------------------------------------------------------------------- writers

    def upsert_survey(self, survey: Survey) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO surveys (survey_id, schema_version, started_at, ended_at,
                    recording_start_epoch_ms, video_path, device_json, app_version,
                    notes, ingest_stats_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(survey_id) DO UPDATE SET
                    ended_at=excluded.ended_at,
                    video_path=excluded.video_path,
                    device_json=excluded.device_json,
                    app_version=excluded.app_version,
                    notes=excluded.notes,
                    ingest_stats_json=excluded.ingest_stats_json
                """,
                (
                    survey.survey_id,
                    survey.schema_version,
                    _iso(survey.started_at),
                    _iso(survey.ended_at) if survey.ended_at else None,
                    survey.recording_start_epoch_ms,
                    survey.video_path,
                    json.dumps(survey.device),
                    survey.app_version,
                    survey.notes,
                    json.dumps(survey.ingest_stats),
                ),
            )

    def insert_frames(self, frames: Sequence[Frame]) -> None:
        rows = [
            (
                f.frame_id,
                f.survey_id,
                f.video_time_s,
                f.t_epoch_ms,
                f.width,
                f.height,
                f.image_path,
                f.observation_location.lat if f.observation_location else None,
                f.observation_location.lon if f.observation_location else None,
                f.observation_location.method.value if f.observation_location else None,
                f.observation_location.uncertainty_m if f.observation_location else None,
                f.speed_mps,
                f.heading_deg,
                f.quality.value,
                json.dumps(f.quality_scores),
            )
            for f in frames
        ]
        with self.transaction() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO frames (frame_id, survey_id, video_time_s,
                   t_epoch_ms, width, height, image_path, obs_lat, obs_lon, obs_method,
                   obs_uncertainty_m, speed_mps, heading_deg, quality, quality_scores_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )

    def insert_detections(self, detections: Sequence[Detection]) -> None:
        rows = [
            (
                d.detection_id,
                d.frame_id,
                d.survey_id,
                d.damage_class.value,
                d.confidence,
                d.bbox.x1,
                d.bbox.y1,
                d.bbox.x2,
                d.bbox.y2,
                json.dumps(d.mask) if d.mask else None,
                d.model_id,
                d.track_id,
            )
            for d in detections
        ]
        with self.transaction() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO detections (detection_id, frame_id, survey_id,
                   damage_class, confidence, x1, y1, x2, y2, mask_json, model_id, track_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )

    def upsert_defects(self, defects: Sequence[Defect]) -> None:
        with self.transaction() as conn:
            for d in defects:
                conn.execute(
                    """
                    INSERT INTO defects (defect_id, schema_version, damage_class, lat, lon,
                        location_method, uncertainty_m, road_source, road_segment_id, road_name,
                        confidence, severity, severity_source, status, trend, observation_count,
                        survey_ids_json, first_seen, last_seen, representative_frame_id,
                        representative_image_path, model_id, processing_run_id, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(defect_id) DO UPDATE SET
                        lat=excluded.lat, lon=excluded.lon,
                        location_method=excluded.location_method,
                        uncertainty_m=excluded.uncertainty_m,
                        confidence=excluded.confidence,
                        severity=excluded.severity,
                        severity_source=excluded.severity_source,
                        status=excluded.status,
                        trend=excluded.trend,
                        observation_count=excluded.observation_count,
                        survey_ids_json=excluded.survey_ids_json,
                        last_seen=excluded.last_seen,
                        representative_frame_id=excluded.representative_frame_id,
                        representative_image_path=excluded.representative_image_path,
                        updated_at=excluded.updated_at
                    """,
                    (
                        d.defect_id,
                        d.schema_version,
                        d.damage_class.value,
                        d.location.lat,
                        d.location.lon,
                        d.location.method.value,
                        d.location.uncertainty_m,
                        d.road.source if d.road else None,
                        d.road.segment_id if d.road else None,
                        d.road.name if d.road else None,
                        d.confidence,
                        d.severity.value,
                        d.severity_source.value,
                        d.status.value,
                        d.trend.value,
                        d.observation_count,
                        json.dumps(d.survey_ids),
                        _iso(d.first_seen),
                        _iso(d.last_seen),
                        d.representative_frame_id,
                        d.representative_image_path,
                        d.model_id,
                        d.processing_run_id,
                        _iso(d.created_at),
                        _iso(d.updated_at),
                    ),
                )
                self._index_defect(conn, d)

    @staticmethod
    def _index_defect(conn: sqlite3.Connection, defect: Defect) -> None:
        """Maintain the R*Tree entry for a defect.

        The rtree table keys on an integer, so a side table maps defect_id to a stable
        integer rowid. Entries are stored as degenerate (point) rectangles; radius
        queries widen the *query* box instead, which keeps insertion trivial.
        """
        cur = conn.execute(
            "SELECT rowid_int FROM defect_rowid WHERE defect_id = ?", (defect.defect_id,)
        )
        row = cur.fetchone()
        if row is None:
            cur = conn.execute(
                "INSERT INTO defect_rowid (defect_id) VALUES (?)", (defect.defect_id,)
            )
            rowid = cur.lastrowid
        else:
            rowid = row["rowid_int"]
        conn.execute(
            "INSERT OR REPLACE INTO defects_rtree (id, min_lat, max_lat, min_lon, max_lon) "
            "VALUES (?,?,?,?,?)",
            (
                rowid,
                defect.location.lat,
                defect.location.lat,
                defect.location.lon,
                defect.location.lon,
            ),
        )

    def insert_observations(self, observations: Sequence[DefectObservation]) -> None:
        rows = [
            (
                o.observation_id,
                o.defect_id,
                o.survey_id,
                o.track_id,
                json.dumps(o.detection_ids),
                _iso(o.observed_at),
                o.confidence,
                o.location.lat,
                o.location.lon,
                o.location.method.value,
                o.location.uncertainty_m,
                o.representative_frame_id,
            )
            for o in observations
        ]
        with self.transaction() as conn:
            conn.executemany(
                """INSERT OR REPLACE INTO defect_observations (observation_id, defect_id,
                   survey_id, track_id, detection_ids_json, observed_at, confidence,
                   lat, lon, location_method, uncertainty_m, representative_frame_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )

    def append_review(self, review: Review) -> None:
        """Append a review. There is deliberately no update or delete counterpart."""
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO reviews (review_id, defect_id, action, reviewer, reviewed_at,
                   previous_value_json, new_value_json, note) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    review.review_id,
                    review.defect_id,
                    review.action.value,
                    review.reviewer,
                    _iso(review.reviewed_at),
                    json.dumps(review.previous_value) if review.previous_value else None,
                    json.dumps(review.new_value) if review.new_value else None,
                    review.note,
                ),
            )

    def upsert_model_version(self, model: ModelVersion) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO model_versions (model_id, name, architecture,
                   framework, framework_version, weights_path, weights_origin, weights_license,
                   dataset_id, training_data_licenses_json, distribution_allowed, classes_json,
                   git_commit, created_at, metrics_json, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    model.model_id,
                    model.name,
                    model.architecture,
                    model.framework,
                    model.framework_version,
                    model.weights_path,
                    model.weights_origin,
                    model.weights_license,
                    model.dataset_id,
                    json.dumps(model.training_data_licenses),
                    int(model.distribution_allowed),
                    json.dumps([c.value for c in model.classes]),
                    model.git_commit,
                    _iso(model.created_at),
                    json.dumps(model.metrics),
                    model.notes,
                ),
            )

    def upsert_processing_run(self, run: ProcessingRun) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO processing_runs (run_id, survey_id, started_at,
                   finished_at, model_id, git_commit, config_json, frames_decoded,
                   frames_sampled, frames_rejected_quality, detections, tracks, defects,
                   errors_json, warnings_json, duration_s)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run.run_id,
                    run.survey_id,
                    _iso(run.started_at),
                    _iso(run.finished_at) if run.finished_at else None,
                    run.model_id,
                    run.git_commit,
                    json.dumps(run.config),
                    run.frames_decoded,
                    run.frames_sampled,
                    run.frames_rejected_quality,
                    run.detections,
                    run.tracks,
                    run.defects,
                    json.dumps(run.errors),
                    json.dumps(run.warnings),
                    run.duration_s,
                ),
            )

    # -------------------------------------------------------------------- readers

    def get_defect(self, defect_id: str) -> Defect | None:
        cur = self._conn.execute("SELECT * FROM defects WHERE defect_id = ?", (defect_id,))
        row = cur.fetchone()
        return self._row_to_defect(row) if row else None

    def list_defects(
        self,
        *,
        damage_class: DamageClass | None = None,
        status: DefectStatus | None = None,
        min_confidence: float | None = None,
        survey_id: str | None = None,
        limit: int | None = None,
    ) -> list[Defect]:
        sql = "SELECT * FROM defects WHERE 1=1"
        params: list[Any] = []
        if damage_class is not None:
            sql += " AND damage_class = ?"
            params.append(damage_class.value)
        if status is not None:
            sql += " AND status = ?"
            params.append(status.value)
        if min_confidence is not None:
            sql += " AND confidence >= ?"
            params.append(min_confidence)
        if survey_id is not None:
            # survey_ids is a JSON array; LIKE on the quoted id is adequate at MVP
            # scale and avoids requiring the JSON1 extension.
            sql += " AND survey_ids_json LIKE ?"
            params.append(f'%"{survey_id}"%')
        sql += " ORDER BY first_seen, defect_id"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        return [self._row_to_defect(r) for r in self._conn.execute(sql, params)]

    def defects_near(self, lat: float, lon: float, radius_m: float) -> list[tuple[Defect, float]]:
        """Defects within ``radius_m``, nearest first, with their distances.

        Index-then-refine: the R*Tree narrows to a bounding box (cheap, approximate),
        then true great-circle distance filters the corners off the box (exact).
        """
        min_lat, max_lat, min_lon, max_lon = bounding_box(LatLon(lat, lon), radius_m)
        cur = self._conn.execute(
            """
            SELECT d.* FROM defects_rtree r
            JOIN defect_rowid m ON m.rowid_int = r.id
            JOIN defects d ON d.defect_id = m.defect_id
            WHERE r.max_lat >= ? AND r.min_lat <= ?
              AND r.max_lon >= ? AND r.min_lon <= ?
            """,
            (min_lat, max_lat, min_lon, max_lon),
        )
        origin = LatLon(lat, lon)
        out: list[tuple[Defect, float]] = []
        for row in cur:
            defect = self._row_to_defect(row)
            dist = haversine_m(origin, LatLon(defect.location.lat, defect.location.lon))
            if dist <= radius_m:
                out.append((defect, dist))
        out.sort(key=lambda pair: pair[1])
        return out

    def observations_for(self, defect_id: str) -> list[DefectObservation]:
        cur = self._conn.execute(
            "SELECT * FROM defect_observations WHERE defect_id = ? ORDER BY observed_at",
            (defect_id,),
        )
        return [
            DefectObservation(
                observation_id=r["observation_id"],
                defect_id=r["defect_id"],
                survey_id=r["survey_id"],
                track_id=r["track_id"],
                detection_ids=json.loads(r["detection_ids_json"]),
                observed_at=_parse_iso(r["observed_at"]),
                confidence=r["confidence"],
                location=GeoPoint(
                    lat=r["lat"],
                    lon=r["lon"],
                    method=LocationMethod(r["location_method"]),
                    uncertainty_m=r["uncertainty_m"],
                ),
                representative_frame_id=r["representative_frame_id"],
            )
            for r in cur
        ]

    def reviews_for(self, defect_id: str) -> list[sqlite3.Row]:
        return list(
            self._conn.execute(
                "SELECT * FROM reviews WHERE defect_id = ? ORDER BY reviewed_at", (defect_id,)
            )
        )

    def count(self, table: str) -> int:
        allowed = {
            "surveys",
            "frames",
            "detections",
            "defects",
            "defect_observations",
            "reviews",
            "model_versions",
            "processing_runs",
        }
        if table not in allowed:
            # Table names cannot be parameterised, so an allowlist is the only safe
            # way to accept one from a caller.
            raise ValueError(f"unknown table: {table}")
        return int(self._conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

    @staticmethod
    def _row_to_defect(row: sqlite3.Row) -> Defect:
        from roadeye.domain.enums import DefectTrend
        from roadeye.domain.models import RoadSegmentRef

        road = None
        if row["road_segment_id"]:
            road = RoadSegmentRef(
                source=row["road_source"] or "unknown",
                segment_id=row["road_segment_id"],
                name=row["road_name"],
            )
        return Defect(
            defect_id=row["defect_id"],
            schema_version=row["schema_version"],
            damage_class=DamageClass(row["damage_class"]),
            location=GeoPoint(
                lat=row["lat"],
                lon=row["lon"],
                method=LocationMethod(row["location_method"]),
                uncertainty_m=row["uncertainty_m"],
            ),
            road=road,
            confidence=row["confidence"],
            severity=Severity(row["severity"]),
            severity_source=SeveritySource(row["severity_source"]),
            status=DefectStatus(row["status"]),
            trend=DefectTrend(row["trend"]),
            observation_count=row["observation_count"],
            survey_ids=json.loads(row["survey_ids_json"]),
            first_seen=_parse_iso(row["first_seen"]),
            last_seen=_parse_iso(row["last_seen"]),
            representative_frame_id=row["representative_frame_id"],
            representative_image_path=row["representative_image_path"],
            model_id=row["model_id"],
            processing_run_id=row["processing_run_id"],
            created_at=_parse_iso(row["created_at"]),
            updated_at=_parse_iso(row["updated_at"]),
        )
