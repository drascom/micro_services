# Villa Bridge for Android

The Android application can turn an always-powered tablet into a standalone
Villa Bridge host or a lightweight monitor for a Linux/Raspberry Pi host. On
startup it sends a Villa Bridge-specific LAN discovery request and verifies the
answer against the remote `/api/discovery` identity. When a server is found,
Android opens its dashboard and does not start the embedded MQTT broker, Zigbee
core, or Matterbridge. Otherwise it starts the complete local stack.
Firewalled Linux/Pi hosts must allow UDP `8093` and the configured dashboard
TCP port on the trusted home LAN.

## What a tablet host can and cannot promise

A tablet is a good dashboard and a workable controller, but it is not the equal
of an always-on server. Android may kill the runtime process when memory runs
short, a system update restarts the device, and battery or sleep restrictions
can pause background work. Villa Bridge pushes back on all three — a foreground
service with wake, Wi-Fi and multicast locks, a boot receiver, and automatic
restart after an unexpected exit — but the operating system has the final word.

The restart is deliberately **not** unconditional. Because the Node runtime
shares the process, restarting it means killing that process, so an unbounded
retry would turn a bad configuration into a tablet that reboots itself every
second. The watchdog backs off exponentially (5 s, 10 s, 20 s … capped at
300 s), gives up after eight consecutive failures, and resets its counter once
the runtime has stayed healthy for two minutes. Its state is reported to the
runtime, so `/api/android/diagnostics` and the wall tablet card in the panel
settings both show how many restarts happened and why. A user-pressed Start or
a device reboot clears the counter.

Battery optimisation is asked for, never forced: the panel explains what the
exemption buys and offers the button, and a refusal is a supported state.
Keep the tablet on mains power and expect an occasional restart. If the house should keep running while nobody is
watching it, put the controller on a Linux/Raspberry Pi host and let the tablet
run as a monitor; LAN discovery makes that switch by itself.

**Use a network coordinator.** On Android the coordinator must be reachable over
TCP — an SLZB or another Zigbee-over-IP adapter. A USB coordinator stick is not
supported here: the core reports `zigbee.serialSupported` as false whenever the
node role is `android`, so the dashboard offers only a `tcp://` address. The
serial path exists solely for a Linux/Raspberry Pi installation. A network
coordinator also survives the tablet being unplugged, moved, or replaced.

## Runtime Architecture

| Port | Service |
| --- | --- |
| `127.0.0.1:8091` | Villa Bridge dashboard and device API |
| `127.0.0.1:8092` | Android readiness, diagnostics, and local-only probes |
| UDP `8093` | Villa Bridge Linux/Raspberry Pi discovery |
| LAN IP `:1883` | Embedded Aedes MQTT 3.1.1 broker for Home Assistant |
| `8283` | Matterbridge frontend/WebSocket used by the core |
| UDP `5540` | Matter commissioning server |

In direct mode, the core connects from the tablet to the configured TCP
coordinator, commonly an SLZB endpoint on port `6638`. It publishes the
Zigbee2MQTT-compatible retained topics consumed by Matterbridge. Matterbridge
then exposes the devices to Alexa, Apple Home, and other Matter controllers.
The broker listens on all tablet interfaces, while the core and Matterbridge
connect through `127.0.0.1`. When the provisioned Zigbee2MQTT YAML contains
both `mqtt.user` and `mqtt.password`, those credentials are mandatory for all
MQTT clients, including Home Assistant. Omitting either value preserves
anonymous compatibility and is unsuitable for an untrusted LAN.

The native start screen follows the runtime diagnostics endpoint and reports
the active MQTT, Zigbee, Matter, or remote-dashboard stage. Monitor mode keeps
only the small Android foreground host and its loopback diagnostics endpoint
running. Linux systemd installations identify themselves with
`VILLA_BRIDGE_NODE_ROLE=server`; the embedded Android core uses `android`.
Settings can stop the entire Android runtime while leaving a native
**Start Villa Bridge** control available. The desired stopped/running state
survives app and tablet restarts.

To keep first launch practical on low-power tablets, the Node project is
packaged as one compressed `tgz` asset. Source maps and TypeScript declaration
files are removed from the runtime-only bundle, then the archive is streamed
into app-private storage with path traversal checks.

## Build and Test

Host requirements are JDK 17, Android SDK 35, NDK `27.2.12479018`, CMake
`3.22.1`, Node.js 22, npm, and `adb`.

```sh
npm install
npm test
npm run android:build
JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home \
  apps/android/gradlew -p apps/android lintDebug testDebugUnitTest
```

Install only on the explicitly approved Nokia T10 test tablet:

```sh
adb devices -l
adb install -r apps/android/app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n com.villabridge.android/.MainActivity
```

Do not install on another Android device or operate a live Zigbee device
without explicit approval. During physical verification, only the designated
**Balcony LED Controller** may be switched; all other device checks must remain
read-only.

## First start and provisioning

Nothing has to be pushed to the tablet. On its first start the shared runtime
seeds its own device-protected `villa-data` directory from the templates bundled
in the APK (`apps/runtime/templates/`), generating a Zigbee network key,
extended pan id, pan id and MQTT password that are unique to this tablet:

```text
villa-data/
├── provisioning.json
├── config/villa-bridge.yaml
└── zigbee/configuration.yaml
```

Existing files are never overwritten, so an already provisioned tablet — and its
legacy `android-provisioning.json` — is left alone.

The seeded Zigbee configuration carries a placeholder coordinator address
(`tcp://192.0.2.10:6638`). While it is in place the runtime starts in **setup
mode**: the dashboard is served and the setup wizard runs, but no coordinator is
opened and Matterbridge stays down. Enter the coordinator address in the wizard;
it writes the real configuration and restarts the runtime.

Because the generated parameters describe a **new** Zigbee network, a tablet
pointed at a coordinator that already runs a house must be given the existing
network parameters first — on Android that currently means pushing an existing
`configuration.yaml` with `adb` as shown below, before the first start.

For a debug installation, stop the app and stream files directly into its
private storage; this avoids leaving credentials in shared tablet storage:

```sh
adb shell am force-stop com.villabridge.android
adb shell run-as com.villabridge.android mkdir -p \
  /data/user_de/0/com.villabridge.android/files/villa-data/config \
  /data/user_de/0/com.villabridge.android/files/villa-data/zigbee

adb exec-out run-as com.villabridge.android sh -c \
  'cat > /data/user_de/0/com.villabridge.android/files/villa-data/zigbee/configuration.yaml' \
  < /secure/path/to/zigbee2mqtt-configuration.yaml
adb shell run-as com.villabridge.android chmod 700 \
  /data/user_de/0/com.villabridge.android/files/villa-data \
  /data/user_de/0/com.villabridge.android/files/villa-data/config \
  /data/user_de/0/com.villabridge.android/files/villa-data/zigbee
adb shell run-as com.villabridge.android chmod 600 \
  /data/user_de/0/com.villabridge.android/files/villa-data/zigbee/configuration.yaml
```

That pushed configuration must contain the cloned network parameters and TCP
coordinator settings required by direct mode. `/api/ready` returns `503` while
the runtime is still in `android-setup` mode (waiting for the wizard) and with
an `unprovisioned` reason if a configuration file cannot be read at all.

For Home Assistant, use the tablet's stable LAN address, port `1883`, and the
same `mqtt.user`/`mqtt.password` values. Do not put credentials in screenshots,
logs, committed YAML, or shell history.

### Kiosk mode

A wall tablet is usually meant to show only Villa Bridge. Android has two
different mechanisms for that, and they are not equally strong:

- **Screen pinning** is available on any tablet (Settings → Security → App
  pinning). It needs no preparation, but the user can leave it by holding Back
  and Overview. Treat it as a convenience, not a lock.
- **Real kiosk (lock task) mode** requires the app to be the **device owner**,
  and a device owner can only be set on a tablet that has no accounts on it yet:

  ```sh
  adb shell dpm set-device-owner com.villabridge.android/<device-admin-receiver>
  ```

  On a tablet that already has a Google account the command fails, and the only
  way forward is a factory reset. So this belongs at the start of the install
  procedure, not at the end: factory-reset the tablet, skip the account during
  setup, connect Wi-Fi, enable USB debugging, install the APK, then set the
  device owner. Undoing it later needs another reset.

  Villa Bridge does not ship a device-admin receiver yet, so today the app
  cannot be made device owner and screen pinning is the practical option. The
  APK does hide the system bars and manages the screen itself, which covers
  most of what a wall panel needs.

### Screen behaviour

Brightness is a **panel** control, and a daily one: the slider lives in the app
menu (the hamburger), with no admin restriction, because a resident should be
able to dim the wall tablet. The setup-grade parts — how long before the
screensaver appears, whether the screen dims with it, and the night window —
live in Settings → Wall tablet. There is no Android settings screen.

There is **one idle timer**, and it is the panel's existing screensaver (clock,
date, weather). When it opens the host dims; a touch dismisses it and the host
restores. The host runs no timer of its own and swallows no touches — the
screensaver overlay already absorbs the first tap, so nothing is toggled by
accident.

Brightness is written to the tablet's **own** setting
(`Settings.System.SCREEN_BRIGHTNESS`), so the value matches what Android
settings show and a change made there is picked up by the panel on the next
menu open. That needs the special `WRITE_SETTINGS` permission, requested from
the panel with an explanation and never forced. **Without it the app keeps
working** and falls back to window-level brightness, which dims only the Villa
Bridge screen; the menu says so and offers the permission button.

Automatic brightness is handled explicitly: while the light sensor drives the
screen, a written value is taken back on the next reading and the slider would
appear dead. So the first panel-set brightness switches
`SCREEN_BRIGHTNESS_MODE` to manual, and — because that is a visible device
change — the menu states that automatic brightness was switched off.

Provisioning files, credentials, and network keys must never enter Git. Keep
app-private device-protected directories at mode `0700` and files at `0600`.
The debug Matterbridge frontend on port `8283` may be visible to the LAN; do
not use the debug build on an untrusted network. A release build must restrict
or authenticate that listener before distribution.

## Inspect the Runtime

```sh
adb forward tcp:18091 tcp:8091
adb forward tcp:18092 tcp:8092
curl --fail http://127.0.0.1:18092/api/ready
curl --fail http://127.0.0.1:18091/api/health
adb logcat -s VillaCoreService:V VillaNode:V AndroidRuntime:E
```

A ready system reports successful MQTT self-test, `core.ready: true`, and
`matter.ready: true`. Follow the
[Android test checklist](../../docs/android-test-checklist.md) for the complete
non-destructive and approved-device checks.

## Release Limitations

- The APK embeds Node.js Mobile `18.20.4`; Android therefore pins
  `zigbee-herdsman@6.2.0` and `zigbee-herdsman-converters@25.50.0`. The desktop
  dependency set is newer and requires Node 20+. Device-definition parity must
  be audited before release.
- The pinned `libnode.so` has 4 KiB ELF segment alignment. A production build
  for newer 16 KiB-page Android devices requires a rebuilt native runtime.
- The pinned Matterbridge/Node 18 dependency tree currently has unresolved npm
  audit findings. Run `npm audit --omit=dev` and resolve or formally assess all
  high/moderate findings before distribution.
- Release packaging must restrict access to Matterbridge port `8283`; the debug
  listener is not an acceptable security boundary on an untrusted LAN.
- Successful local tests do not by themselves prove coordinator recovery,
  every device mapping, Matter commissioning, or long-running tablet power
  behavior; complete the checklist on the approved hardware.
