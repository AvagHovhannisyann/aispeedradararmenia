/**
 * RoadEye Collector — a field instrument, not a camera app.
 *
 * Design constraints that shape this screen:
 *
 * * **Foreground only.** The app stays open for the whole survey. That is a
 *   deliberate scope decision (ADR-002): iOS background location needs a development
 *   build, Always authorization and background modes, none of which the MVP requires.
 * * **No interaction while driving.** Start before moving, stop after stopping. The
 *   recording screen is a status display, not a control panel.
 * * **Problems must be visible before the drive, not after.** A survey ruined by poor
 *   GPS or a full disk is 30 minutes wasted, so status is large and unambiguous.
 */

import { CameraView, useCameraPermissions } from 'expo-camera';
import * as FileSystem from 'expo-file-system';
import * as Location from 'expo-location';
import { activateKeepAwakeAsync, deactivateKeepAwake } from 'expo-keep-awake';
import { StatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Platform, Pressable, StyleSheet, Text, View } from 'react-native';

import {
  BUNDLE_SCHEMA_VERSION,
  LocationRecord,
  SurveyPaths,
  appendLocations,
  createSurvey,
  describeAccuracy,
  toLocationRecord,
  writeDevice,
  writeManifest,
  writeRoute,
} from './src/survey';

const APP_VERSION = '0.1.0';

/** How often buffered fixes are flushed to disk. See src/survey.ts on the trade-off. */
const FLUSH_INTERVAL_MS = 5000;

/** Refuse to start a survey with less than this free, rather than fail mid-drive. */
const MIN_FREE_BYTES = 2 * 1024 * 1024 * 1024;

type Phase = 'idle' | 'starting' | 'recording' | 'saving' | 'done' | 'error';

export default function App() {
  const [cameraPermission, requestCameraPermission] = useCameraPermissions();
  const [locationGranted, setLocationGranted] = useState(false);
  const [accuracyAuthorization, setAccuracyAuthorization] = useState<string>('unknown');

  const [phase, setPhase] = useState<Phase>('idle');
  const [message, setMessage] = useState<string | null>(null);
  const [elapsedS, setElapsedS] = useState(0);
  const [fixCount, setFixCount] = useState(0);
  const [lastAccuracy, setLastAccuracy] = useState<number | undefined>();
  const [lastSpeed, setLastSpeed] = useState<number | undefined>();
  const [surveyId, setSurveyId] = useState<string | null>(null);

  const cameraRef = useRef<CameraView>(null);
  const pathsRef = useRef<SurveyPaths | null>(null);
  const bufferRef = useRef<LocationRecord[]>([]);
  const subscriptionRef = useRef<Location.LocationSubscription | null>(null);
  const flushTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const tickTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const startedAtRef = useRef<Date | null>(null);
  /**
   * The video time anchor, captured once at recording start and never recomputed.
   * `started_at` (when the user tapped START) and `recording_start_epoch_ms` (when the
   * camera actually began) are different instants, and conflating them shifts every
   * position in the survey by the camera's startup delay.
   */
  const recordingStartRef = useRef<number | null>(null);

  useEffect(() => {
    (async () => {
      const { status, ios } = await Location.requestForegroundPermissionsAsync();
      setLocationGranted(status === 'granted');
      // SDK 55+ reports whether iOS granted full or reduced accuracy. A survey under
      // reduced accuracy is recoverable but must be recorded, not guessed at later.
      if (ios?.scopedAccuracy) setAccuracyAuthorization(ios.scopedAccuracy);
    })();
  }, []);

  const flush = useCallback(async () => {
    const paths = pathsRef.current;
    if (!paths || bufferRef.current.length === 0) return;
    const pending = bufferRef.current;
    bufferRef.current = [];
    try {
      await appendLocations(paths.locations, pending);
    } catch (err) {
      // Put them back; a transient write failure should not silently drop fixes.
      bufferRef.current = pending.concat(bufferRef.current);
      setMessage(`Location write failed: ${String(err)}`);
    }
  }, []);

  const start = useCallback(async () => {
    setPhase('starting');
    setMessage(null);

    try {
      if (!cameraPermission?.granted) {
        const result = await requestCameraPermission();
        if (!result.granted) throw new Error('Camera permission is required.');
      }
      if (!locationGranted) throw new Error('Location permission is required.');

      const free = await FileSystem.getFreeDiskStorageAsync();
      if (free < MIN_FREE_BYTES) {
        throw new Error(
          `Only ${(free / 1e9).toFixed(1)} GB free. Free up space before surveying.`,
        );
      }

      const baseDir = FileSystem.documentDirectory;
      if (!baseDir) throw new Error('No writable document directory.');

      const { surveyId: id, paths } = await createSurvey(baseDir);
      pathsRef.current = paths;
      setSurveyId(id);

      await activateKeepAwakeAsync();

      // Start location first so fixes bracket the whole video rather than beginning
      // after it. Interpolation can handle GPS that starts early; it cannot invent
      // positions for video recorded before the first fix.
      subscriptionRef.current = await Location.watchPositionAsync(
        {
          accuracy: Location.Accuracy.BestForNavigation,
          timeInterval: 1000,
          distanceInterval: 0,
        },
        (loc) => {
          const record = toLocationRecord(loc);
          bufferRef.current.push(record);
          setFixCount((n) => n + 1);
          setLastAccuracy(record.accuracy_m);
          setLastSpeed(record.speed_mps);
        },
      );

      const startedAt = new Date();
      startedAtRef.current = startedAt;

      // Captured immediately before recording begins. This is the anchor for every
      // downstream timestamp; see src/survey.ts.
      const recordingStartEpochMs = Date.now();
      recordingStartRef.current = recordingStartEpochMs;

      await writeRoute(paths.route, {
        schema_version: BUNDLE_SCHEMA_VERSION,
        route_id: id,
        started_at: startedAt.toISOString(),
        recording_start_epoch_ms: recordingStartEpochMs,
        camera_facing: 'back',
        requested_video_quality: '1080p',
        app_version: APP_VERSION,
      });

      await writeDevice(paths.device, {
        os: Platform.OS,
        os_version: String(Platform.Version),
        orientation: 'landscape',
        location_accuracy_authorization: accuracyAuthorization,
      });

      flushTimerRef.current = setInterval(flush, FLUSH_INTERVAL_MS);
      tickTimerRef.current = setInterval(() => {
        setElapsedS(Math.floor((Date.now() - recordingStartEpochMs) / 1000));
      }, 1000);

      setPhase('recording');

      // recordAsync resolves only when recording stops, so it is intentionally not
      // awaited here.
      cameraRef.current
        ?.recordAsync()
        .then(async (video) => {
          if (video?.uri && pathsRef.current) {
            await FileSystem.moveAsync({ from: video.uri, to: pathsRef.current.video });
          }
        })
        .catch((err) => setMessage(`Recording error: ${String(err)}`));
    } catch (err) {
      setPhase('error');
      setMessage(String(err instanceof Error ? err.message : err));
      await cleanup();
    }
  }, [cameraPermission, requestCameraPermission, locationGranted, accuracyAuthorization, flush]);

  const cleanup = useCallback(async () => {
    subscriptionRef.current?.remove();
    subscriptionRef.current = null;
    if (flushTimerRef.current) clearInterval(flushTimerRef.current);
    if (tickTimerRef.current) clearInterval(tickTimerRef.current);
    flushTimerRef.current = null;
    tickTimerRef.current = null;
    try {
      deactivateKeepAwake();
    } catch {
      // Keep-awake may already be released; not worth failing a survey over.
    }
  }, []);

  const stop = useCallback(async () => {
    setPhase('saving');
    try {
      cameraRef.current?.stopRecording();
      await cleanup();
      await flush();

      const paths = pathsRef.current;
      if (paths && startedAtRef.current && recordingStartRef.current != null) {
        // Rewritten only to add ended_at. The recording anchor is carried through
        // unchanged — recomputing it here would silently offset the whole survey.
        await writeRoute(paths.route, {
          schema_version: BUNDLE_SCHEMA_VERSION,
          route_id: surveyId!,
          started_at: startedAtRef.current.toISOString(),
          ended_at: new Date().toISOString(),
          recording_start_epoch_ms: recordingStartRef.current,
          camera_facing: 'back',
          requested_video_quality: '1080p',
          app_version: APP_VERSION,
        });
        await writeManifest(paths.manifest, [
          'route.json',
          'locations.jsonl',
          'device.json',
          'video.mp4',
        ]);
      }
      setPhase('done');
    } catch (err) {
      setPhase('error');
      setMessage(String(err));
    }
  }, [cleanup, flush, surveyId]);

  useEffect(() => () => void cleanup(), [cleanup]);

  const gps = describeAccuracy(lastAccuracy);
  const permissionsReady = Boolean(cameraPermission?.granted) && locationGranted;

  return (
    <View style={styles.container}>
      <StatusBar style="light" />

      {permissionsReady ? (
        <CameraView ref={cameraRef} style={styles.camera} facing="back" mode="video" />
      ) : (
        <View style={[styles.camera, styles.center]}>
          <Text style={styles.help}>
            RoadEye needs camera and location access to record a survey.
          </Text>
          <Pressable style={styles.button} onPress={() => requestCameraPermission()}>
            <Text style={styles.buttonText}>Grant permissions</Text>
          </Pressable>
        </View>
      )}

      <View style={styles.hud}>
        <Text style={styles.timer}>{formatElapsed(elapsedS)}</Text>
        <Text style={[styles.status, !gps.usable && styles.warn]}>{gps.label}</Text>
        <Text style={styles.status}>
          {lastSpeed != null ? `${(lastSpeed * 3.6).toFixed(0)} km/h` : '– km/h'} · {fixCount} fixes
        </Text>
        {surveyId ? <Text style={styles.survey}>{surveyId}</Text> : null}
        {message ? <Text style={styles.error}>{message}</Text> : null}
      </View>

      <View style={styles.controls}>
        {phase === 'recording' ? (
          <Pressable style={[styles.button, styles.stop]} onPress={stop}>
            <Text style={styles.buttonText}>STOP</Text>
          </Pressable>
        ) : (
          <Pressable
            style={[styles.button, !permissionsReady && styles.disabled]}
            onPress={start}
            disabled={!permissionsReady || phase === 'starting' || phase === 'saving'}
          >
            <Text style={styles.buttonText}>
              {phase === 'saving' ? 'SAVING…' : phase === 'done' ? 'START NEW SURVEY' : 'START'}
            </Text>
          </Pressable>
        )}
        <Text style={styles.safety}>Start before you drive. Do not touch the phone while moving.</Text>
      </View>
    </View>
  );
}

function formatElapsed(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#000' },
  camera: { flex: 1 },
  center: { alignItems: 'center', justifyContent: 'center', padding: 24 },
  hud: {
    position: 'absolute',
    top: 24,
    left: 24,
    backgroundColor: 'rgba(0,0,0,0.65)',
    padding: 12,
    borderRadius: 8,
  },
  timer: { color: '#fff', fontSize: 34, fontVariant: ['tabular-nums'], fontWeight: '600' },
  status: { color: '#eee', fontSize: 15 },
  warn: { color: '#ffb020', fontWeight: '700' },
  survey: { color: '#888', fontSize: 11, marginTop: 6 },
  error: { color: '#ff5252', fontSize: 13, marginTop: 6, maxWidth: 320 },
  controls: { position: 'absolute', bottom: 32, alignSelf: 'center', alignItems: 'center' },
  button: {
    backgroundColor: '#1e88e5',
    paddingHorizontal: 44,
    paddingVertical: 18,
    borderRadius: 40,
  },
  stop: { backgroundColor: '#e53935' },
  disabled: { backgroundColor: '#555' },
  buttonText: { color: '#fff', fontSize: 20, fontWeight: '700', letterSpacing: 1 },
  help: { color: '#fff', fontSize: 16, textAlign: 'center', marginBottom: 20 },
  safety: { color: '#aaa', fontSize: 12, marginTop: 10 },
});
