/**
 * Survey bundle writer — the filesystem half of the contract with the processor.
 *
 * Everything pure lives in `bundle.ts`, which has no `expo-*` imports and is therefore
 * testable with `node --test` and no install. This file is deliberately thin: it is the
 * part that cannot be tested here, so it should contain as little judgement as possible.
 */

import * as FileSystem from 'expo-file-system';

import {
  type LocationRecord,
  type DeviceMetadata,
  type RouteMetadata,
  buildManifest,
  encodeLocations,
  makeSurveyId,
  randomSuffix,
} from './bundle';

export {
  BUNDLE_SCHEMA_VERSION,
  type DeviceMetadata,
  type LocationRecord,
  type RouteMetadata,
  buildManifest,
  buildRoute,
  describeAccuracy,
  describeStorage,
  formatElapsed,
  toLocationRecord,
} from './bundle';

export interface SurveyPaths {
  root: string;
  route: string;
  locations: string;
  device: string;
  manifest: string;
  video: string;
}

/** Create a new survey directory and return the paths within it. */
export async function createSurvey(
  baseDir: string,
): Promise<{ surveyId: string; paths: SurveyPaths }> {
  const surveyId = makeSurveyId(new Date(), randomSuffix());
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

/**
 * Serialises appends, per file.
 *
 * `appendLocations` is read-modify-write, because expo-file-system has no append
 * primitive. Two overlapping calls therefore both read the same contents and the second
 * write discards the first one's records — silently, with no error anywhere. That is
 * easy to trigger in the real app: fixes flush on a five-second timer, `stop()` flushes
 * again immediately, and the read-write pair gets slower as the file grows.
 *
 * Chaining every append for a path onto the previous one makes the sequence
 * last-writer-wins over *nothing*, because there is never more than one in flight.
 */
const appendQueues = new Map<string, Promise<void>>();

export async function appendLocations(
  path: string,
  records: LocationRecord[],
): Promise<void> {
  if (records.length === 0) return;
  const payload = encodeLocations(records);

  const previous = appendQueues.get(path) ?? Promise.resolve();
  // The queue must advance even when an append fails, or one error would wedge every
  // later write for this file. The caller still sees the rejection via `next`.
  const next = previous.catch(() => {}).then(() => writeAppend(path, payload));
  appendQueues.set(
    path,
    next.catch(() => {}),
  );
  return next;
}

async function writeAppend(path: string, payload: string): Promise<void> {
  const info = await FileSystem.getInfoAsync(path);
  if (!info.exists) {
    await FileSystem.writeAsStringAsync(path, payload);
    return;
  }

  // Read-modify-write is the portable option. Location logs are small — a 30-minute
  // drive at 1 Hz is well under 200 KB — so this is acceptable at MVP scale. If surveys
  // get much longer, switch to numbered chunk files rather than growing a single
  // rewritten file.
  const existing = await FileSystem.readAsStringAsync(path);
  await FileSystem.writeAsStringAsync(path, existing + payload);
}

/** Only for tests and for teardown between surveys. */
export function resetAppendQueues(): void {
  appendQueues.clear();
}

export async function writeRoute(path: string, route: RouteMetadata): Promise<void> {
  await FileSystem.writeAsStringAsync(path, JSON.stringify(route, null, 2));
}

export async function writeDevice(path: string, device: DeviceMetadata): Promise<void> {
  await FileSystem.writeAsStringAsync(path, JSON.stringify(device, null, 2));
}

/**
 * Write the file inventory.
 *
 * `hasVideo` is passed in rather than assumed. The manifest is the bundle's statement
 * about itself, and one that lists a video that was never written sends the processor
 * looking for a file that does not exist.
 */
export async function writeManifest(
  path: string,
  options: { hasVideo: boolean; hasFrames?: boolean },
): Promise<void> {
  await FileSystem.writeAsStringAsync(path, JSON.stringify(buildManifest(options), null, 2));
}

/** Whether a file exists and has non-zero size, for confirming the video landed. */
export async function fileIsPresent(path: string): Promise<boolean> {
  try {
    const info = await FileSystem.getInfoAsync(path, { size: true });
    return info.exists && (info.size ?? 0) > 0;
  } catch {
    return false;
  }
}
