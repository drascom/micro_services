import type { DeviceControlView, DeviceView, JsonObject } from "./types.js";

export interface HomeAssistantDiscoveryMessage {
  topic: string;
  payload: JsonObject;
}

const safeId = (value: string): string => value.toLowerCase().replace(/[^a-z0-9_-]+/g, "_");

const deviceInfo = (device: DeviceView): JsonObject => ({
  identifiers: [device.id],
  name: device.name,
  manufacturer: device.vendor ?? "Zigbee",
  model: device.model ?? "Unknown"
});

const common = (device: DeviceView, uniqueId: string, baseTopic: string): JsonObject => ({
  unique_id: uniqueId,
  device: deviceInfo(device),
  origin: { name: "Villa Bridge", sw_version: "0.1.0" },
  availability_mode: "all",
  availability: [
    {
      topic: `${baseTopic}/bridge/state`,
      value_template: "{{ value_json.state }}",
      payload_available: "online",
      payload_not_available: "offline"
    },
    {
      topic: `${baseTopic}/${device.sourceName}/availability`,
      value_template: "{{ value_json.state if value_json is mapping else value }}",
      payload_available: "online",
      payload_not_available: "offline"
    }
  ]
});

const message = (
  component: string,
  uniqueId: string,
  payload: JsonObject
): HomeAssistantDiscoveryMessage => ({
  topic: `homeassistant/${component}/${safeId(uniqueId)}/config`,
  payload
});

const switchDiscovery = (
  device: DeviceView,
  control: DeviceControlView,
  baseTopic: string
): HomeAssistantDiscoveryMessage => {
  const uniqueId = `villa_${safeId(device.id)}_${safeId(control.id)}`;
  return message("switch", uniqueId, {
    ...common(device, uniqueId, baseTopic),
    name: control.name,
    state_topic: `${baseTopic}/${device.sourceName}`,
    command_topic: `${baseTopic}/${device.sourceName}/set`,
    value_template: `{{ value_json.${control.property} }}`,
    payload_on: JSON.stringify({ [control.property]: control.valueOn ?? "ON" }),
    payload_off: JSON.stringify({ [control.property]: control.valueOff ?? "OFF" }),
    state_on: control.valueOn ?? "ON",
    state_off: control.valueOff ?? "OFF",
    entity_category: control.adminOnly ? "config" : undefined
  });
};

const jsonCommand = (property: string, value: string): string =>
  JSON.stringify({ [property]: value });

const specialDiscovery = (
  device: DeviceView,
  baseTopic: string
): HomeAssistantDiscoveryMessage[] => {
  const result: HomeAssistantDiscoveryMessage[] = [];
  const stateTopic = `${baseTopic}/${device.sourceName}`;
  const commandTopic = `${stateTopic}/set`;
  const cover = device.controls.find((control) => control.kind === "cover");
  const position = device.controls.find((control) => control.kind === "position");
  if (cover || position) {
    const uniqueId = `villa_${safeId(device.id)}_cover`;
    const stateProperty = cover?.property;
    result.push(message("cover", uniqueId, {
      ...common(device, uniqueId, baseTopic),
      name: device.name,
      state_topic: cover ? stateTopic : undefined,
      command_topic: cover ? commandTopic : undefined,
      value_template: stateProperty ? `{{ value_json.${stateProperty} }}` : undefined,
      payload_open: stateProperty ? jsonCommand(stateProperty, "OPEN") : undefined,
      payload_close: stateProperty ? jsonCommand(stateProperty, "CLOSE") : undefined,
      payload_stop: stateProperty ? jsonCommand(stateProperty, "STOP") : undefined,
      state_open: cover ? "OPEN" : undefined,
      state_opening: cover ? "OPENING" : undefined,
      state_closed: cover ? "CLOSE" : undefined,
      state_closing: cover ? "CLOSING" : undefined,
      position_topic: position ? stateTopic : undefined,
      position_template: position ? `{{ value_json.${position.property} }}` : undefined,
      set_position_topic: position ? commandTopic : undefined,
      set_position_template: position
        ? `{"${position.property}": {{ position }}}`
        : undefined
    }));
  }

  const setpoint = device.controls.find((control) => control.kind === "climate");
  const climateMode = device.controls.find((control) =>
    control.kind === "select" && control.id.startsWith("climate:")
  );
  if (setpoint || climateMode) {
    const uniqueId = `villa_${safeId(device.id)}_climate`;
    result.push(message("climate", uniqueId, {
      ...common(device, uniqueId, baseTopic),
      name: device.name,
      temperature_state_topic: setpoint ? stateTopic : undefined,
      temperature_state_template: setpoint ? `{{ value_json.${setpoint.property} }}` : undefined,
      temperature_command_topic: setpoint ? commandTopic : undefined,
      temperature_command_template: setpoint
        ? `{"${setpoint.property}": {{ value }}}`
        : undefined,
      current_temperature_topic: typeof device.state.local_temperature === "number" ? stateTopic : undefined,
      current_temperature_template: typeof device.state.local_temperature === "number"
        ? "{{ value_json.local_temperature }}"
        : undefined,
      min_temp: setpoint?.min,
      max_temp: setpoint?.max,
      temp_step: setpoint?.step,
      mode_state_topic: climateMode ? stateTopic : undefined,
      mode_state_template: climateMode ? `{{ value_json.${climateMode.property} }}` : undefined,
      mode_command_topic: climateMode ? commandTopic : undefined,
      mode_command_template: climateMode
        ? `{"${climateMode.property}": "{{ value }}"}`
        : undefined,
      modes: climateMode?.values
    }));
  }

  for (const control of device.controls.filter((item) => item.kind === "lock")) {
    const uniqueId = `villa_${safeId(device.id)}_${safeId(control.id)}`;
    result.push(message("lock", uniqueId, {
      ...common(device, uniqueId, baseTopic),
      name: control.name || device.name,
      state_topic: stateTopic,
      command_topic: commandTopic,
      value_template: `{{ value_json.${control.property} }}`,
      payload_lock: jsonCommand(control.property, String(control.values?.[0] ?? "LOCK")),
      payload_unlock: jsonCommand(control.property, String(control.values?.[1] ?? "UNLOCK")),
      state_locked: String(control.values?.[0] ?? "LOCK"),
      state_unlocked: String(control.values?.[1] ?? "UNLOCK")
    }));
  }

  const fan = device.controls.find((control) => control.kind === "fan");
  const fanSpeed = device.controls.find((control) =>
    control.id.startsWith("fan:") && control.kind !== "fan"
  );
  if (fan) {
    const uniqueId = `villa_${safeId(device.id)}_fan`;
    result.push(message("fan", uniqueId, {
      ...common(device, uniqueId, baseTopic),
      name: device.name,
      state_topic: stateTopic,
      command_topic: commandTopic,
      state_value_template: `{{ value_json.${fan.property} }}`,
      payload_on: jsonCommand(fan.property, String(fan.valueOn ?? "ON")),
      payload_off: jsonCommand(fan.property, String(fan.valueOff ?? "OFF")),
      preset_mode_state_topic: fanSpeed?.values?.length ? stateTopic : undefined,
      preset_mode_value_template: fanSpeed?.values?.length
        ? `{{ value_json.${fanSpeed.property} }}`
        : undefined,
      preset_mode_command_topic: fanSpeed?.values?.length ? commandTopic : undefined,
      preset_mode_command_template: fanSpeed?.values?.length
        ? `{"${fanSpeed.property}": "{{ value }}"}`
        : undefined,
      preset_modes: fanSpeed?.values
    }));
  }

  for (const control of device.controls.filter((item) => item.kind === "siren")) {
    const uniqueId = `villa_${safeId(device.id)}_${safeId(control.id)}`;
    result.push(message("siren", uniqueId, {
      ...common(device, uniqueId, baseTopic),
      name: control.name || device.name,
      state_topic: stateTopic,
      command_topic: commandTopic,
      state_value_template: `{{ value_json.${control.property} }}`,
      payload_on: jsonCommand(control.property, String(control.valueOn ?? "ON")),
      payload_off: jsonCommand(control.property, String(control.valueOff ?? "OFF")),
      state_on: String(control.valueOn ?? "ON"),
      state_off: String(control.valueOff ?? "OFF")
    }));
  }

  for (const control of device.controls.filter((item) =>
    item.kind === "number" && !item.id.startsWith("fan:")
  )) {
    const uniqueId = `villa_${safeId(device.id)}_${safeId(control.id)}`;
    result.push(message("number", uniqueId, {
      ...common(device, uniqueId, baseTopic),
      name: control.name,
      state_topic: stateTopic,
      command_topic: commandTopic,
      value_template: `{{ value_json.${control.property} }}`,
      command_template: `{"${control.property}": {{ value }}}`,
      min: control.min,
      max: control.max,
      step: control.step,
      unit_of_measurement: control.unit,
      entity_category: control.adminOnly ? "config" : undefined
    }));
  }

  for (const control of device.controls.filter((item) =>
    item.kind === "select" && !item.id.startsWith("climate:") && !item.id.startsWith("fan:")
  )) {
    const uniqueId = `villa_${safeId(device.id)}_${safeId(control.id)}`;
    result.push(message("select", uniqueId, {
      ...common(device, uniqueId, baseTopic),
      name: control.name,
      state_topic: stateTopic,
      command_topic: commandTopic,
      value_template: `{{ value_json.${control.property} }}`,
      command_template: `{"${control.property}": "{{ value }}"}`,
      options: control.values,
      entity_category: control.adminOnly ? "config" : undefined
    }));
  }
  return result;
};

export function buildHomeAssistantDiscovery(
  devices: DeviceView[],
  baseTopic: string
): HomeAssistantDiscoveryMessage[] {
  const result: HomeAssistantDiscoveryMessage[] = [];
  for (const device of devices) {
    result.push(...specialDiscovery(device, baseTopic));
    const switches = device.controls.filter((control) => control.kind === "switch");
    const main = switches.find((control) => control.id === "main");
    const brightness = device.controls.find((control) => control.kind === "level" && control.id.startsWith("main:"));
    const temperature = device.controls.find((control) => control.kind === "temperature" && control.id === "main:temperature");
    const color = device.controls.find((control) => control.kind === "color" && control.id.startsWith("main:"));
    const isLight = Boolean(main && (brightness || temperature || color));

    if (main && isLight) {
      const uniqueId = `villa_${safeId(device.id)}_light`;
      const supportedColorModes = [
        color ? "xy" : "",
        temperature ? "color_temp" : "",
        !color && !temperature && brightness ? "brightness" : ""
      ].filter(Boolean);
      result.push(message("light", uniqueId, {
        ...common(device, uniqueId, baseTopic),
        name: device.name,
        schema: "json",
        state_topic: `${baseTopic}/${device.sourceName}`,
        command_topic: `${baseTopic}/${device.sourceName}/set`,
        brightness: Boolean(brightness),
        brightness_scale: brightness?.max ?? 254,
        min_mireds: temperature?.min,
        max_mireds: temperature?.max,
        supported_color_modes: supportedColorModes
      }));
    }
    for (const control of switches) {
      if (isLight && control.id === "main") continue;
      result.push(switchDiscovery(device, control, baseTopic));
    }

    const binarySensors: Array<[string, string, string, boolean]> = [
      ["contact", "Door", "door", true],
      ["presence", "Presence", "occupancy", false],
      ["occupancy", "Motion", "motion", false],
      ["smoke", "Smoke", "smoke", false],
      ["carbon_monoxide", "Carbon monoxide", "carbon_monoxide", false],
      ["battery_low", "Battery low", "battery", false]
    ];
    for (const [property, name, deviceClass, invert] of binarySensors) {
      if (!(property in device.state)) continue;
      const uniqueId = `villa_${safeId(device.id)}_${property}`;
      const expression = invert
        ? `{{ 'OFF' if value_json.${property} else 'ON' }}`
        : `{{ 'ON' if value_json.${property} else 'OFF' }}`;
      result.push(message("binary_sensor", uniqueId, {
        ...common(device, uniqueId, baseTopic),
        name,
        device_class: deviceClass,
        state_topic: `${baseTopic}/${device.sourceName}`,
        value_template: expression,
        payload_on: "ON",
        payload_off: "OFF"
      }));
    }

    const sensors: Array<[string, string, string | null, string | null]> = [
      ["battery", "Battery", "battery", "%"],
      ["linkquality", "Signal", null, "lqi"],
      ["illuminance", "Illuminance", "illuminance", "lx"],
      ["power", "Power", "power", "W"],
      ["voltage", "Voltage", "voltage", "V"],
      ["current", "Current", "current", "A"],
      ["energy", "Energy", "energy", "kWh"]
    ];
    for (const [property, name, deviceClass, unit] of sensors) {
      if (typeof device.state[property] !== "number") continue;
      const uniqueId = `villa_${safeId(device.id)}_${property}`;
      result.push(message("sensor", uniqueId, {
        ...common(device, uniqueId, baseTopic),
        name,
        device_class: deviceClass ?? undefined,
        unit_of_measurement: unit,
        state_class: "measurement",
        state_topic: `${baseTopic}/${device.sourceName}`,
        value_template: `{{ value_json.${property} }}`
      }));
    }
    const actionTypes = [
      ...(device.actionTypes ?? []),
      ...(typeof device.state.action === "string" ? [device.state.action] : [])
    ].filter((value, index, values) => value.length > 0 && values.indexOf(value) === index);
    if (actionTypes.length > 0) {
      const uniqueId = `villa_${safeId(device.id)}_action`;
      result.push(message("sensor", uniqueId, {
        ...common(device, uniqueId, baseTopic),
        name: "Last action",
        icon: "mdi:gesture-tap-button",
        state_topic: `${baseTopic}/${device.sourceName}`,
        value_template: "{{ value_json.action }}"
      }));
      const eventUniqueId = `villa_${safeId(device.id)}_action_event`;
      result.push(message("event", eventUniqueId, {
        ...common(device, eventUniqueId, baseTopic),
        name: "Action",
        icon: "mdi:gesture-tap-button",
        state_topic: `${baseTopic}/${device.sourceName}`,
        event_types: actionTypes,
        value_template: "{{ {'event_type': value_json.action} | to_json }}"
      }));
    }
  }
  return result;
}
