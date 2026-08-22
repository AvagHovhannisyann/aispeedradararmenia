/**
 * The survey bundle contract — everything about it that is pure.
 *
 * Deliberately free of `expo-*` imports. That is not tidiness: it is what lets this
 * file be tested with `node --test` and no `npm install` at all, which matters because
 * the collector is the one component that has never run on a device. The parts that
 * touch the filesystem live in `survey.ts` and stay as thin as they can be.
 *
 * The bundle format, not this app, is the real interface. A future native iOS or
 * Android collector is "compatible" precisely when it emits a bundle that
 * `roadeye.ingest.bundle.load_bundle` accepts. Keep this file and
 * `src/roadeye/ingest/bundle.py` in step; they are two ends of one wire, and
 * `tests/integration/test_collector_contract.py` checks that they agree.
 */

/** Bundle format version. Must match BUNDLE_SCHEMA_VERSION in the Python package. */
export const BUNDLE_SCHEMA_VERSION = 1;

/** Survey ids reach filesystem paths, so the processor restricts them to this set. */
export const SAFE_ID = /^[A-Za-z0-9._-]+$/;

export interface LocationRecord {
  /** Device clock in epoch milliseconds. The single time reference for everything. */
  t: number;
  lat: number;
  lon: number;
  accuracy_m?: number;
  speed_mps?: number;
  heading_deg?: number;
  altitude_m?: number;
}

export interface RouteMetadata {
  schema_version: number;
  route_id: string;
  started_at: string;
  ended_at?: string;
  recording_start_epoch_ms: number;
  camera_facing: 'back' | 'front';
  requested_video_quality: string;
  app_version: string;
}

export interface DeviceMetadata {
  model?: string;
  os?: string;
  os_version?: string;
  orientation?: string;
  /**
   * Whether iOS granted full or reduced location accuracy. A survey recorded under
   * reduced accuracy is not automatically useless, but the processor must know rather
   * than guess.
   */
  location_accuracy_authorization?: string;
}

/* ------------------------------------------------------------------ survey id */

/**
 * Build a survey id.
 *
 * `now` and `suffix` are injected rather than read from the ambient clock so this is
 * deterministic under test. The id embeds a UTC timestamp so surveys sort
 * chronologically in a file listing, which is what someone triaging a phone full of
 * drives actually needs.
 */
export function makeSurveyId(now: Date, suffix: string): string {
  const stamp = now.toISOString().replace(/[:.]/g, '-');
  const id = `survey_${stamp}_${suffix}`;
  if (!SAFE_ID.test(id)) {
    throw new Error(`generated survey id is not filesystem-safe: ${id}`);
  }
  return id;
}

export function randomSuffix(): string {
  return Math.random().toString(36).slice(2, 8).padEnd(6, '0');
}

/* --------------------------------------------------------------- location fixes */

/**
 * Convert an Expo location object into a bundle record.
 *
 * Expo reports speed and heading as -1 when unavailable on some platforms. Passing -1
 * through as a real value would corrupt distance-based sampling and heading-based map
 * matching, so it is normalised to "absent" here — at the boundary, once.
 *
 * Zero is kept in every case: a speed of 0 means stopped, a heading of 0 means due
 * north, and an accuracy of 0 is simply a very good fix. Only negatives are sentinels.
 */
export function toLocationRecord(location: {
  timestamp: number;
  coords: {
    latitude: number;
    longitude: number;
    accuracy?: number | null;
    speed?: number | null;
    heading?: number | null;
    altitude?: number | null;
  };
}): LocationRecord {
  const { coords } = location;
  const record: LocationRecord = {
    t: Math.round(location.timestamp),
    lat: coords.latitude,
    lon: coords.longitude,
  };

  if (coords.accuracy != null && coords.accuracy >= 0) record.accuracy_m = coords.accuracy;
  if (coords.speed != null && coords.speed >= 0) record.speed_mps = coords.speed;
  if (coords.heading != null && coords.heading >= 0) record.heading_deg = coords.heading;
  if (coords.altitude != null) record.altitude_m = coords.altitude;

  return record;
}

/**
 * Serialise fixes as JSON Lines.
 *
 * One object per line, always ending in a newline. A phone that runs out of storage or
 * is force-quit mid-write leaves a truncated final line, which costs one GPS sample.
 * Buffering the whole drive and writing a JSON array at the end would instead lose the
 * entire survey, because a truncated array is unparseable.
 */
export function encodeLocations(records: LocationRecord[]): string {
  if (records.length === 0) return '';
  return records.map((r) => JSON.stringify(r)).join('\n') + '\n';
}

/* -------------------------------------------------------------------- metadata */

export function buildRoute(fields: {
  routeId: string;
  startedAt: Date;
  endedAt?: Date;
  recordingStartEpochMs: number;
  appVersion: string;
  cameraFacing?: 'back' | 'front';
  requestedVideoQuality?: string;
}): RouteMetadata {
  const route: RouteMetadata = {
    schema_version: BUNDLE_SCHEMA_VERSION,
    route_id: fields.routeId,
    started_at: fields.startedAt.toISOString(),
    recording_start_epoch_ms: fields.recordingStartEpochMs,
    camera_facing: fields.cameraFacing ?? 'back',
    requested_video_quality: fields.requestedVideoQuality ?? '1080p',
    app_version: fields.appVersion,
  };
  if (fields.endedAt) route.ended_at = fields.endedAt.toISOString();
  return route;
}

/**
 * The file inventory.
 *
 * `hasVideo` is a parameter rather than an assumption. Listing `video.mp4`
 * unconditionally would make the manifest a claim about a file that may not exist —
 * and a bundle that lies about its own contents is worse than one that admits a
 * recording failed.
 */
export function buildManifest(options: { hasVideo: boolean; hasFrames?: boolean }): {
  schema_version: number;
  files: string[];
} {
  const files = ['route.json', 'locations.jsonl', 'device.json'];
  if (options.hasVideo) files.push('video.mp4');
  if (options.hasFrames) files.push('frames');
  return { schema_version: BUNDLE_SCHEMA_VERSION, files };
}

/* --------------------------------------------------------------------- status */

/** Human-readable GPS quality, for the recording screen. */
export function describeAccuracy(accuracyM: number | undefined): {
  label: string;
  usable: boolean;
} {
  if (accuracyM == null) return { label: 'GPS: acquiring…', usable: false };
  if (accuracyM <= 10) return { label: `GPS: good (±${accuracyM.toFixed(0)} m)`, usable: true };
  if (accuracyM <= 25) return { label: `GPS: fair (±${accuracyM.toFixed(0)} m)`, usable: true };
  // The processor drops fixes worse than 25 m by default, so the driver should be told
  // now rather than discovering an unusable survey after the drive.
  return { label: `GPS: POOR (±${accuracyM.toFixed(0)} m)`, usable: false };
}

/**
 * Bytes per minute of 1080p30 video, used to turn free space into recordable minutes.
 *
 * **This is an estimate, not a measurement.** Phone encoders vary widely — roughly
 * 8 Mbps on iPhone 1080p30, and 10-20 Mbps on many Android devices. 120 MB/min is
 * deliberately at the pessimistic end, because the cost of over-estimating is a
 * truncated survey and the cost of under-estimating is being told to free up space you
 * did not need to.
 *
 * Replace it with a measured figure after the first real drive: divide the video's byte
 * size by its duration. `docs/COLLECTION_PROTOCOL.md` says so too.
 */
export const VIDEO_BYTES_PER_MINUTE = 120 * 1024 * 1024;

/**
 * Shortest survey worth starting, in minutes.
 *
 * The M1 acceptance criterion is a 30-minute survey, but refusing to start anything
 * shorter would block the 5-minute shakedown drive that should come first. This is the
 * floor below which the app declines, not the target.
 */
export const MIN_SURVEY_MINUTES = 10;

/**
 * Turn free bytes into "how long can I record?".
 *
 * The previous fixed 2 GB floor was quietly wrong: at a pessimistic 120 MB/min it buys
 * about 17 minutes, so a survey meeting M1's own 30-minute acceptance criterion would
 * have run the phone out of storage partway through — and a truncated video is a wasted
 * drive, discovered afterwards.
 */
export function describeStorage(freeBytes: number): {
  label: string;
  minutes: number;
  ok: boolean;
} {
  const minutes = Math.floor(freeBytes / VIDEO_BYTES_PER_MINUTE);
  const gb = freeBytes / 1e9;
  if (minutes < MIN_SURVEY_MINUTES) {
    return {
      label: `Only ${gb.toFixed(1)} GB free — about ${minutes} min of video. Free up space.`,
      minutes,
      ok: false,
    };
  }
  return { label: `${gb.toFixed(1)} GB free — about ${minutes} min of video`, minutes, ok: true };
}

export function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}
