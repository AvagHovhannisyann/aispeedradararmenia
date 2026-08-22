/**
 * Tests for the collector's pure logic.
 *
 * Run with no install at all:
 *
 *     node --experimental-strip-types --test apps/collector/tests/
 *
 * That matters more here than anywhere else in the repository. The collector is the one
 * component that has **never run on a device**, so it is the one place where a bug is
 * discovered by a wasted 30-minute drive rather than by a stack trace. Anything that can
 * be checked without a phone should be.
 *
 * What is *not* covered: the camera, the GPS subscription, and the filesystem. Those
 * live in `survey.ts` and `App.tsx` and need a device. The value of splitting the pure
 * half out is that the untestable remainder is as small and as dumb as possible.
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  BUNDLE_SCHEMA_VERSION,
  MIN_SURVEY_MINUTES,
  SAFE_ID,
  VIDEO_BYTES_PER_MINUTE,
  buildManifest,
  buildRoute,
  describeAccuracy,
  describeStorage,
  encodeLocations,
  formatElapsed,
  makeSurveyId,
  randomSuffix,
  toLocationRecord,
} from '../src/bundle.ts';

const AT = new Date('2026-08-22T19:17:38.123Z');

function fix(overrides: Record<string, unknown> = {}) {
  return {
    timestamp: 1_755_000_000_000,
    coords: {
      latitude: 40.1823,
      longitude: 44.5149,
      accuracy: 6,
      speed: 9.7,
      heading: 87,
      altitude: 1010,
      ...overrides,
    },
  };
}

describe('survey id', () => {
  test('is filesystem-safe', () => {
    assert.ok(SAFE_ID.test(makeSurveyId(AT, 'a1b2c3')));
  });

  test('embeds a sortable UTC timestamp', () => {
    const early = makeSurveyId(new Date('2026-08-22T08:00:00.000Z'), 'aaaaaa');
    const late = makeSurveyId(new Date('2026-08-22T19:00:00.000Z'), 'aaaaaa');
    assert.ok(early < late, 'ids must sort chronologically in a file listing');
  });

  test('contains no colons or dots', () => {
    // Both appear in an ISO timestamp and both are trouble in a path — a colon is
    // illegal on some filesystems and a leading dot hides the directory.
    const id = makeSurveyId(AT, 'a1b2c3');
    assert.ok(!id.includes(':'), id);
    assert.ok(!id.includes('.'), id);
  });

  test('a random suffix is always six safe characters', () => {
    // Math.random().toString(36) can be short when the value rounds; an id shorter than
    // expected is not a correctness problem, but a padded one keeps listings aligned.
    for (let i = 0; i < 500; i++) {
      const suffix = randomSuffix();
      assert.equal(suffix.length, 6);
      assert.ok(SAFE_ID.test(suffix), suffix);
    }
  });
});

describe('location records', () => {
  test('keeps a good fix intact', () => {
    const record = toLocationRecord(fix());
    assert.deepEqual(record, {
      t: 1_755_000_000_000,
      lat: 40.1823,
      lon: 44.5149,
      accuracy_m: 6,
      speed_mps: 9.7,
      heading_deg: 87,
      altitude_m: 1010,
    });
  });

  test('drops the -1 sentinel rather than passing it through', () => {
    // Expo reports -1 for speed and heading when unavailable. A -1 speed would look
    // like reverse motion to distance sampling; a -1 heading would rotate every map
    // match. Normalised at the boundary, once.
    const record = toLocationRecord(fix({ speed: -1, heading: -1, accuracy: -1 }));
    assert.equal(record.speed_mps, undefined);
    assert.equal(record.heading_deg, undefined);
    assert.equal(record.accuracy_m, undefined);
  });

  test('keeps zero, which is a real value in every case', () => {
    // Stopped at a light, pointing due north, and a very good fix. Treating zero as
    // missing would delete the red-light samples the stationary guard depends on.
    const record = toLocationRecord(fix({ speed: 0, heading: 0, accuracy: 0 }));
    assert.equal(record.speed_mps, 0);
    assert.equal(record.heading_deg, 0);
    assert.equal(record.accuracy_m, 0);
  });

  test('keeps a negative altitude, which is below sea level not missing', () => {
    const record = toLocationRecord(fix({ altitude: -12 }));
    assert.equal(record.altitude_m, -12);
  });

  test('handles null coords fields', () => {
    const record = toLocationRecord(fix({ speed: null, heading: null, altitude: null }));
    assert.equal(record.speed_mps, undefined);
    assert.equal(record.altitude_m, undefined);
  });

  test('rounds a fractional device timestamp', () => {
    const record = toLocationRecord({ ...fix(), timestamp: 1_755_000_000_000.7 });
    assert.equal(record.t, 1_755_000_000_001);
    assert.ok(Number.isInteger(record.t));
  });
});

describe('JSON Lines encoding', () => {
  test('one object per line, newline-terminated', () => {
    const payload = encodeLocations([
      { t: 1, lat: 40, lon: 44 },
      { t: 2, lat: 41, lon: 45 },
    ]);
    assert.equal(payload, '{"t":1,"lat":40,"lon":44}\n{"t":2,"lat":41,"lon":45}\n');
  });

  test('every line parses on its own', () => {
    // The point of JSONL: a truncated write costs one sample, not the survey. A JSON
    // array truncated mid-write is unparseable and loses everything.
    const payload = encodeLocations([
      { t: 1, lat: 40, lon: 44 },
      { t: 2, lat: 41, lon: 45 },
    ]);
    const lines = payload.trimEnd().split('\n');
    assert.equal(lines.length, 2);
    for (const line of lines) JSON.parse(line);
  });

  test('an empty batch writes nothing', () => {
    assert.equal(encodeLocations([]), '');
  });

  test('no embedded newline can break the line framing', () => {
    // JSON.stringify escapes newlines, so a stray one in a value cannot split a record
    // across two lines. Asserted because the whole format rests on it.
    const payload = encodeLocations([{ t: 1, lat: 40, lon: 44 } as never]);
    assert.equal(payload.split('\n').filter(Boolean).length, 1);
  });
});

describe('route metadata', () => {
  test('carries the schema version the processor checks', () => {
    const route = buildRoute({
      routeId: 'survey_x',
      startedAt: AT,
      recordingStartEpochMs: 1_755_000_000_000,
      appVersion: '0.1.0',
    });
    assert.equal(route.schema_version, BUNDLE_SCHEMA_VERSION);
  });

  test('omits ended_at until the survey ends', () => {
    const route = buildRoute({
      routeId: 'survey_x',
      startedAt: AT,
      recordingStartEpochMs: 1,
      appVersion: '0.1.0',
    });
    assert.ok(!('ended_at' in route));
  });

  test('started_at and the recording anchor stay independent', () => {
    // They are different instants: one is when the user tapped START, the other when
    // the camera actually began. Conflating them shifts every position in the survey by
    // the camera's startup delay — at 50 km/h, one second is ~14 m.
    const anchor = AT.getTime() + 1400;
    const route = buildRoute({
      routeId: 'survey_x',
      startedAt: AT,
      recordingStartEpochMs: anchor,
      appVersion: '0.1.0',
    });
    assert.equal(route.started_at, AT.toISOString());
    assert.equal(route.recording_start_epoch_ms, anchor);
    assert.notEqual(Date.parse(route.started_at), route.recording_start_epoch_ms);
  });
});

describe('manifest', () => {
  test('lists the video only when there is one', () => {
    // A manifest that always claims video.mp4 sends the processor looking for a file a
    // failed recording never wrote. The bundle's statement about itself must be true.
    assert.ok(buildManifest({ hasVideo: true }).files.includes('video.mp4'));
    assert.ok(!buildManifest({ hasVideo: false }).files.includes('video.mp4'));
  });

  test('always lists the three files a bundle cannot do without', () => {
    const files = buildManifest({ hasVideo: false }).files;
    for (const name of ['route.json', 'locations.jsonl', 'device.json']) {
      assert.ok(files.includes(name), name);
    }
  });

  test('can declare an extracted frames directory', () => {
    assert.ok(buildManifest({ hasVideo: false, hasFrames: true }).files.includes('frames'));
  });
});

describe('GPS quality shown to the driver', () => {
  test('acquiring is not usable', () => {
    assert.equal(describeAccuracy(undefined).usable, false);
  });

  test('good and fair are usable', () => {
    assert.ok(describeAccuracy(4).usable);
    assert.ok(describeAccuracy(20).usable);
  });

  test('worse than the processor accepts is flagged', () => {
    // The pipeline drops fixes above 25 m, so the driver must be told during the drive
    // rather than discovering an empty survey afterwards.
    assert.equal(describeAccuracy(30).usable, false);
    assert.match(describeAccuracy(30).label, /POOR/);
  });

  test('the boundary is inclusive on the usable side', () => {
    assert.ok(describeAccuracy(25).usable);
    assert.equal(describeAccuracy(25.1).usable, false);
  });
});

describe('storage, in minutes rather than bytes', () => {
  test('enough space reports how long you can record', () => {
    const space = describeStorage(12 * VIDEO_BYTES_PER_MINUTE);
    assert.ok(space.ok);
    assert.equal(space.minutes, 12);
  });

  test('too little space refuses and says how little', () => {
    const space = describeStorage(3 * VIDEO_BYTES_PER_MINUTE);
    assert.equal(space.ok, false);
    assert.match(space.label, /Free up space/);
  });

  test('the old 2 GB floor would not have covered a 30-minute survey', () => {
    // Regression for a threshold that was quietly wrong: M1's acceptance criterion is a
    // 30-minute survey, and 2 GB buys about 17 minutes at a pessimistic bitrate. The
    // phone would have filled partway through and truncated the video.
    const twoGigabytes = 2 * 1024 * 1024 * 1024;
    assert.ok(
      describeStorage(twoGigabytes).minutes < 30,
      'a fixed 2 GB floor is not 30 minutes of video',
    );
  });

  test('the floor to start at all is stated, not hidden', () => {
    assert.equal(describeStorage(MIN_SURVEY_MINUTES * VIDEO_BYTES_PER_MINUTE).ok, true);
    assert.equal(
      describeStorage((MIN_SURVEY_MINUTES - 1) * VIDEO_BYTES_PER_MINUTE).ok,
      false,
    );
  });

  test('an empty phone is refused rather than crashing', () => {
    assert.equal(describeStorage(0).ok, false);
    assert.equal(describeStorage(0).minutes, 0);
  });
});

describe('elapsed time', () => {
  test('pads to mm:ss', () => {
    assert.equal(formatElapsed(0), '00:00');
    assert.equal(formatElapsed(65), '01:05');
  });

  test('keeps counting past an hour rather than wrapping', () => {
    // A long survey must not read as 00:30 when it is 1:00:30 — the driver uses this to
    // know how far through the planned route they are.
    assert.equal(formatElapsed(3630), '60:30');
  });
});
