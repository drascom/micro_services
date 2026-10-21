# Nokia T10 Android Standalone Test Checklist

Run this checklist only on the explicitly approved Nokia T10. Do not record or
commit Wi-Fi credentials, MQTT passwords, Zigbee network keys, fabric data, or
public device identifiers. Only the designated **Balcony LED Controller** may
be switched during physical verification; all other devices are read-only.

## 1. Automated Validation

Run from the repository root:

```sh
npm test
npx --yes node@18.20.4 --test \
  apps/runtime/main.test.cjs \
  apps/runtime/orchestration.test.cjs
JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home \
  apps/android/gradlew -p apps/android lintDebug testDebugUnitTest
npm run android:build
```

- [ ] Core tests pass.
- [ ] Node 18 runtime tests pass.
- [ ] Android lint passes; record `NO-SOURCE` if no Kotlin unit tests exist.
- [ ] APK builds without checksum, CMake, NDK, or ABI errors.

## 2. Identify and Install on the Approved Tablet

```sh
adb devices -l
adb shell getprop ro.product.model
adb shell getprop ro.build.version.release
adb shell getprop ro.build.version.sdk
adb shell getprop ro.product.cpu.abilist
adb install -r apps/android/app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n com.villabridge.android/.MainActivity
```

- [ ] ADB state is `device`, not `unauthorized`.
- [ ] Model is Nokia T10 and its API/ABI values are recorded.
- [ ] Foreground-service notification is visible.
- [ ] Battery optimization is disabled only with owner approval.

## 3. Provision Without Exposing Secrets

Use the repository templates and the private-storage commands in
[the Android README](../apps/android/README.md#secure-provisioning). Supply the
live Zigbee2MQTT configuration from a secure path; never copy it into the
repository or shared tablet storage.

- [ ] `android-provisioning.json` enables the intended Matter mode.
- [ ] `villa-bridge.yaml` uses `mode: direct` and loopback MQTT/HTTP endpoints.
- [ ] `zigbee/configuration.yaml` contains the approved cloned network and TCP
      coordinator settings.
- [ ] No credential-bearing temporary file remains outside app-private storage.
- [ ] App-private device-protected directories are `0700`, files containing
      configuration or keys are `0600`, and none are tracked by Git.

## 4. Readiness and Services

```sh
adb forward tcp:18091 tcp:8091
adb forward tcp:18092 tcp:8092
curl --fail http://127.0.0.1:18092/api/ready
curl --fail http://127.0.0.1:18092/api/android/diagnostics
curl --fail http://127.0.0.1:18091/api/health
adb shell dumpsys activity services com.villabridge.android/.NodeRuntimeService
adb logcat -d -s VillaCoreService:V VillaNode:V AndroidRuntime:E
```

- [ ] Node reports `18.20.4` and the expected architecture.
- [ ] Diagnostics are reachable only through tablet loopback port `8092`.
- [ ] `mqtt.listening` and `mqtt.selfTest` are `true`; diagnostics reports only
      whether authentication is required, never the username or password.
- [ ] Home Assistant connects to the tablet LAN IP on `1883` with the
      provisioned credentials; an anonymous client is rejected when both
      `mqtt.user` and `mqtt.password` are present.
- [ ] `core.ready` is `true`; the dashboard/API responds on `127.0.0.1:8091`.
- [ ] `matter.ready` becomes `true`; Matterbridge frontend/WebSocket is on
      `8283` and the commissioning server uses UDP `5540`.
- [ ] Logs contain no native-library, asset, repeated restart, or subsystem
      startup error.

## 5. Coordinator and Device Inventory

First use the local-only TCP probe; it opens and closes a socket without
sending coordinator data:

```sh
curl --fail \
  -H 'content-type: application/json' \
  -d '{"host":"COORDINATOR_IP","port":6638}' \
  http://127.0.0.1:18092/api/android/probe/tcp
curl --fail http://127.0.0.1:18091/api/devices
```

- [ ] TCP probe succeeds with plausible latency.
- [ ] Direct core connects without another Zigbee coordinator owner running.
- [ ] Device UIDs, friendly names, endpoints, signal values, and groups match
      the approved Zigbee2MQTT source-of-truth snapshot.
- [ ] No command is sent during inventory verification.

## 6. Approved Physical Control

Use the API/UI to toggle only the **Balcony LED Controller** off and back to
its original state. Record its UID outside committed logs.

- [ ] The requested endpoint changes once in each direction.
- [ ] State feedback updates in Villa Bridge and retained MQTT state.
- [ ] No other device changes.
- [ ] The controller is restored to its initial state even if a test fails.

## 7. Matter End-to-End

- [ ] Matterbridge exposes the expected Zigbee devices and endpoint breakdown.
- [ ] A commissioning code is available and the bridge advertises over mDNS.
- [ ] Commissioning succeeds with the approved test Matter controller.
- [ ] Names and endpoint mappings match Villa Bridge/Zigbee source-of-truth.
- [ ] Only the Balcony LED Controller is toggled through Matter, then restored.
- [ ] Remove the test fabric afterward unless retention is explicitly approved.

The multicast probe alone is not commissioning proof:

```sh
curl --fail \
  -H 'content-type: application/json' \
  -d '{}' \
  http://127.0.0.1:18092/api/android/probe/multicast
```

## 8. Foreground, Screen-Off, and Reboot Recovery

With approval, leave the powered tablet screen off for at least 15 minutes,
then verify `/api/ready`, uptime, Wi-Fi, MQTT, core, Matter, and the foreground
service again. Reboot testing must also be explicitly approved:

```sh
adb reboot
adb wait-for-device
adb forward tcp:18092 tcp:8092
curl --fail http://127.0.0.1:18092/api/ready
```

- [ ] Screen-off operation remains ready without a crash/restart loop.
- [ ] Service returns after reboot and all five runtime ports recover as
      applicable: `8091`, `8092`, `1883`, `8283`, and UDP `5540`.
- [ ] Coordinator reconnects without duplicating ownership or changing devices.

## 9. Release Gate

- [ ] Results distinguish automated, API-level, Matter-commissioned, and
      physically observed evidence.
- [ ] Node 18 pinning and the Android Zigbee versions (`6.2.0`/`25.50.0`) are
      accepted after device-definition parity review.
- [ ] `libnode.so` is rebuilt for 16 KiB-page compatibility; the current binary
      remains 4 KiB-aligned.
- [ ] `npm audit --omit=dev` findings from pinned Node 18/Matterbridge
      dependencies are resolved or formally risk-assessed.
- [ ] Matterbridge frontend access on `8283` is restricted or authenticated;
      the debug listener has not been approved for an untrusted LAN.
- [ ] Long-running power, coordinator recovery, approved-device control, and
      Matter commissioning all pass on the approved Nokia T10 before release.
