# Villa Bridge

## Brief Introduction and Purpose

Villa Bridge is a mobile-first smart-home dashboard that brings Zigbee devices, Matter and Home Assistant into one control panel. It was written so that everyone in a household can use friendly device names and simple controls without dealing with protocol names, MQTT topics or endpoints. It runs on Debian/Raspberry Pi hosts under `systemd` and as an Android tablet alpha. The full project guide is in [`villa-bridge/README.md`](villa-bridge/README.md).

This branch imports the history of the former `drascom/Villa-Bridge` repository under `villa-bridge/`.

## Quick Install

On a Debian 12 / Raspberry Pi host:

```sh
git clone -b villa-bridge https://github.com/drascom/micro_services.git /opt/micro_services
sudo /opt/micro_services/villa-bridge/apps/linux/install.sh
```

Then open `http://<host-ip>:8091` and finish setup in the dashboard wizard.

## Developing Mode

Requirements: Node.js 22+ and npm; for observing a real system, an MQTT broker and an existing Zigbee2MQTT install. Optional overrides are listed in [`villa-bridge/.env.example`](villa-bridge/.env.example); defaults live in `villa-bridge/config/default.yaml`.

```sh
cd villa-bridge
npm ci
npm run dev    # http://localhost:8091
npm test       # build + structural check
```

## Source Citations

- [Detailed project guide](villa-bridge/README.md)
- [Package manifest](villa-bridge/package.json)
- [Linux and Raspberry Pi guide](villa-bridge/apps/linux/README.md)
- [Android alpha guide](villa-bridge/apps/android/README.md)
