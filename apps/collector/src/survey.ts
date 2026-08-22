/**
 * Survey bundle writer — the collector's half of the contract with the processor.
 *
 * The bundle format, not this app, is the real interface. A future native iOS or
 * Android collector is "compatible" precisely when it emits a bundle that
 * `roadeye.ingest.bundle.load_bundle` accepts. Keep this file and
 * `src/roadeye/ingest/bundle.py` in step; they are two ends of one wire.
 *
 * Two decisions worth understanding before changing anything here:
 *
 * 1. **Locations are written as JSON Lines, appended as they arrive.** A phone that
 *    runs out of storage or is force-quit mid-drive leaves a truncated final line —
 *    which costs one GPS sample. Buffering the whole drive in memory and writing a
 *    JSON array at the end would instead lose the entire survey, because a truncated
 *    array is unparseable.
 *
 * 2. **`recordingStartEpochMs` is captured as close as possible to the first frame.**
 *    It is the anchor for all downstream time arithmetic. Every millisecond of error
 *    here displaces every defect in the survey; at 50 km/h, one second is ~14 m.
 */

import * as FileSystem from 'expo-file-system';

/** Bundle format version. Must match BUNDLE_SCHEMA_VERSION in the Python package. */
export const BUNDLE_SCHEMA_VERSION = 1;

/** Survey ids reach filesystem paths, so the processor restricts them to this set. */
const SAFE_ID = /^[A-Za-z0-9._-]+$/;

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
   * Whether iOS granted full or reduced location accuracy. Expo SDK 55+ reports this
   * in the permission response. A survey recorded under reduced accuracy is not
   * automatically useless, but the processor must know rather than guess.
   */
  location_accuracy_authorization?: string;
}

export interface SurveyPaths {
  root: string;
  route: string;
  locations: string;
  device: string;
  manifest: string;
  video: string;
}

/**
 * Create a new survey directory and return the paths within it.
 *
 * The id embeds a UTC timestamp so surveys sort chronologically in a file listing,
 * which is what someone triaging a phone full of drives actually needs.
 */
export async function createSurvey(baseDir: string): Promise<{ surveyId: string; paths: SurveyPaths }> {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-').replace('Z', 'Z');
  const surveyId = `survey_${stamp}_${randomSuffix()}`;

  if (!SAFE_ID.test(surveyId)) {
    throw new Error(`generated survey id is not filesystem-safe: ${surveyId}`);
  }

  const root = `${baseDir}${baseDir.endsWith('/') ? '' : '/'}${surveyId}/`;
  await FileSystem.makeDirectoryAsync(root, { intermediates: true });

  return {
    surveyId,
    paths: {
      root,
      route: `${root}route.json`,
      locations: `${root}locations.jsonl`,
      device: `${root}device.json`,
      manifest: `${root}manifest.json`,
      video: `${root}video.mp4`,
    },
  };
}

function randomSuffix(): string {
  return Math.random().toString(36).slice(2, 8);
}

/**
 * Append location fixes as JSON Lines.
 *
 * Called on a timer with whatever has accumulated since the last flush, rather than
 * once per fix: one filesystem write per GPS sample would be wasteful, but holding
 * more than a few seconds of samples in memory reintroduces the data-loss risk that
 * JSONL exists to avoid.
 */
export async function appendLocations(path: string, records: LocationRecord[]): Promise<void> {
  if (records.length === 0) return;

  const payload = records.map((r) => JSON.stringify(r)).join('\n') + '\n';

  const info = await FileSystem.getInfoAsync(path);
  if (!info.exists) {
    await FileSystem.writeAsStringAsync(path, payload);
    return;
  }

  // expo-file-system has no append primitive, so read-modify-write is the portable
  // option. Location logs are small (a 30-minute drive at 1 Hz is well under 200 KB),
  // so this is acceptable at MVP scale. If surveys get much longer, switch to
  // numbered chunk files rather than growing a single rewritten file.
  const existing = await FileSystem.readAsStringAsync(path);
  await FileSystem.writeAsStringAsync(path, existing + payload);
}

export async function writeRoute(path: string, route: RouteMetadata): Promise<void> {
  await FileSystem.writeAsStringAsync(path, JSON.stringify(route, null, 2));
}

export async function writeDevice(path: string, device: DeviceMetadata): Promise<void> {
  await FileSystem.writeAsStringAsync(path, JSON.stringify(device, null, 2));
}

export async function writeManifest(path: string, files: string[]): Promise<void> {
  await FileSystem.writeAsStringAsync(
    path,
    JSON.stringify({ schema_version: BUNDLE_SCHEMA_VERSION, files }, null, 2),
  );
}

/**
 * Convert an Expo location object into a bundle record.
 *
 * Expo reports speed and heading as -1 when unavailable on some platforms. Passing
 * -1 through as a real value would corrupt distance-based sampling and heading-based
 * map matching, so it is normalised to "absent" here — at the boundary, once.
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

/** Human-readable GPS quality, for the recording screen. */
export function describeAccuracy(accuracyM: number | undefined): {
  label: string;
  usable: boolean;
} {
  if (accuracyM == null) return { label: 'GPS: acquiring…', usable: false };
  if (accuracyM <= 10) return { label: `GPS: good (±${accuracyM.toFixed(0)} m)`, usable: true };
  if (accuracyM <= 25) return { label: `GPS: fair (±${accuracyM.toFixed(0)} m)`, usable: true };
  // The processor drops fixes worse than 25 m by default, so the driver should be
  // told now rather than discovering an unusable survey after the drive.
  return { label: `GPS: POOR (±${accuracyM.toFixed(0)} m)`, usable: false };
}
