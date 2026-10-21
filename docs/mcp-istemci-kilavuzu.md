# Villa Bridge MCP tool guide

> **Türkçe not (bu belge neden İngilizce).** `docs/` altındaki diğer belgeler Türkçedir; bu belge
> bilinçli bir istisnadır. Burayı **sizin modeliniz okuyacak**: belgeyi olduğu gibi kesip modele
> verebilesiniz diye araç açıklamaları, hata metinleri ve kurallar sunucudaki İngilizce metinlerle
> birebir aynı dilde yazıldı. Protokolün kendisi (adres, kimlik, başlıklar, taşıma hataları)
> `docs/mcp-entegrasyon.md` içinde Türkçe anlatılıyor; burası **araç kılavuzu**dur: hangi araç ne
> yapar, ne bekler, ne döndürür. Sizin bakmanız gereken iki bölüm: **§5 Safety rules** (kilit,
> siren ve `run` neden farklıdır, ajanın yazdığı kuralı panelden nasıl geri alırsınız) ve
> **§6 System prompt** (modele yapıştıracağınız hazır metin).

---

## 1. Connecting

```
POST http://192.168.0.91:8091/mcp
```

Protocol version **`2026-07-28`**, stateless. There is no session, no `initialize` handshake, no
GET/SSE stream. Every request is a single JSON-RPC object and is authenticated on its own.

Every request needs four things:

| What | Value |
|---|---|
| `Authorization` header | `Bearer <agent token>` — created in the panel under **Settings → Assistant access** |
| `MCP-Protocol-Version` header | `2026-07-28`, and it must equal `params._meta["io.modelcontextprotocol/protocolVersion"]` |
| `Mcp-Method` header | must equal the body's `method` |
| `params._meta["io.modelcontextprotocol/clientCapabilities"]` | an object; send `{}` if you have none |

For `tools/call` there is a fifth: the `Mcp-Name` header must equal `params.name`.

Cookie sessions are rejected here on purpose — a browser tab logged into Villa Bridge cannot drive
this endpoint. If you send an `Origin` header, that origin must be listed in `mcp.allowedOrigins`.

Copy-paste check:

```sh
curl -sS http://192.168.0.91:8091/mcp \
  -H "Authorization: Bearer $VILLA_AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -H "MCP-Protocol-Version: 2026-07-28" \
  -H "Mcp-Method: tools/list" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list",
    "params": {
      "_meta": {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}
      }
    }
  }'
```

The tool order is deterministic and there is no pagination:

```json
["list_devices","get_device","set_device","list_automations","get_automation","write_automation","control_automation"]
```

Every successful result carries the server identity:

```json
{"io.modelcontextprotocol/serverInfo":{"name":"villa-bridge","version":"0.1.0"}}
```

Every `tools/call` result has the same shape: `content` (the structured result serialised as text),
`structuredContent` (present only when the call succeeded) and `isError`. The examples below show
`structuredContent` and omit the identical `content` text for brevity.

---

## 2. Device tools

### `list_devices` — start here when the user names a device in words

**Use it for:** "what do I have", "is anything offline", "which lights are on", and above all to
turn a spoken name into an IEEE address. Raw device state is deliberately not exposed.

**Input** — all fields optional:

| Field | Type | Meaning |
|---|---|---|
| `room` | string | Only devices whose room name contains this text (case-insensitive) |
| `category` | string | One of `light`, `switch`, `cover`, `lock`, `climate`, `fan`, `unknown` |
| `onlineOnly` | boolean | Only devices that are currently reachable |
| `search` | string | Free text matched against the device name and address |

**Output:** `count`, and `devices[]` with `id`, `name`, `room`, `category`, `availability` and
`primary` — a one-channel summary (`id`, `name`, `kind`, `value`, optional `unit`), or `null` for a
sensor with no controls.

Request:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "list_devices",
    "arguments": { "room": "salon" },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientCapabilities": {}
    }
  }
}
```

Response (`result`):

```json
{
  "resultType": "complete",
  "structuredContent": {
    "count": 1,
    "devices": [
      {
        "id": "0x00158d0007a1b2c3",
        "name": "Salon lambası",
        "room": "Salon",
        "category": "light",
        "availability": "online",
        "primary": { "id": "main", "name": "Salon lambası", "kind": "switch", "value": true }
      }
    ]
  },
  "isError": false,
  "_meta": { "io.modelcontextprotocol/serverInfo": { "name": "villa-bridge", "version": "0.1.0" } }
}
```

### `get_device` — every channel of one device

**Use it for:** anything you are about to write, and for "how bright is it", "what colour is it".
`id` must be the IEEE address; friendly names are not accepted.

**Output:** `id`, `name`, `room`, `category`, `availability`, `lastSeen`, optional `linkquality`
and `powerSource`, and `controls[]`. Each control has `id`, `name`, `kind`, `value` and, when the
device declares them, `min`, `max`, `step`, `unit`, `values`, `adminOnly`.

`controls[].id` is what `set_device` wants. Common `kind`s: `switch`, `level`, `temperature`,
`color`, `position`, `cover`, `lock`, `fan`, `siren`, `select`, `number`, `climate`.

```json
{ "name": "get_device", "arguments": { "id": "0x00158d0007a1b2c3" } }
```

```json
{
  "id": "0x00158d0007a1b2c3",
  "name": "Salon lambası",
  "room": "Salon",
  "category": "light",
  "availability": "online",
  "lastSeen": "2026-08-08T09:12:44.000Z",
  "linkquality": 84,
  "powerSource": "Mains (single phase)",
  "controls": [
    { "id": "main", "name": "Salon lambası", "kind": "switch", "value": true },
    { "id": "main:brightness", "name": "Parlaklık", "kind": "level", "value": 180, "min": 1, "max": 254 },
    { "id": "main:color", "name": "Renk", "kind": "color", "value": "#ffd7a8" }
  ]
}
```

### `set_device` — change one channel, for real

**Use it for:** turning something on or off, dimming, setting a colour, moving a blind. **It acts
on the real house immediately.** There is no dry run.

**Input:** `id` (IEEE address), `control` (a `controls[].id` from `get_device`), `value`.

What `value` may be, per channel `kind`:

| Kind | Value |
|---|---|
| `switch`, `fan`, `siren` | `true` / `false` (the channel's own `"ON"`/`"OFF"`/`"TOGGLE"` strings are also accepted) |
| `level`, `temperature`, `position`, `number`, `climate` | a number; **clamped** to `min`/`max` and snapped to `step` |
| `color` | a hex string, `"#rrggbb"` — converted to xy for the device |
| `cover`, `lock`, `select` | one of the channel's `values[]` |

**Output:** `id`, `name`, `control`, `kind`, `requested` and `applied`. `applied` is what actually
went to the device — compare the two to see any correction, and report the applied value to the
user rather than the requested one.

An out-of-range number is **not** an error; it is clamped:

```json
{ "name": "set_device", "arguments": { "id": "0x00158d0007a1b2c3", "control": "main:brightness", "value": 400 } }
```

```json
{
  "structuredContent": {
    "id": "0x00158d0007a1b2c3",
    "name": "Salon lambası",
    "control": "main:brightness",
    "kind": "level",
    "requested": 400,
    "applied": 254
  },
  "isError": false
}
```

A wrong channel id tells you the channels that exist, so you can fix the call yourself:

```json
{ "name": "set_device", "arguments": { "id": "0x00158d0007a1b2c3", "control": "loudness", "value": 3 } }
```

```json
{
  "content": [{
    "type": "text",
    "text": "Device `0x00158d0007a1b2c3` has no control `loudness`. Its controls are: `main` (switch), `main:brightness` (level), `main:color` (color)."
  }],
  "isError": true
}
```

Locks and sirens are refused — see §5:

```json
{ "name": "set_device", "arguments": { "id": "0x00158d000b7733fe", "control": "lock:state", "value": "UNLOCK" } }
```

```json
{
  "content": [{
    "type": "text",
    "text": "Control `lock:state` is a lock and cannot be written through the agent endpoint. Locks and sirens are only operated by a person, from the Villa Bridge panel. Do not retry; tell the user to do it themselves."
  }],
  "isError": true
}
```

---

## 3. Automation tools

An automation rule is: **triggers** (when), optional **conditions** (only if), and **actions**
(what). The engine evaluates it on its own from the moment it is saved.

### `list_automations` — what rules exist

**Input:** optional `enabledOnly` (boolean) and `search` (matched against the rule name).

**Output:** `count` and `automations[]`, each with `id`, `name`, `enabled`, readable `triggers[]`,
`conditions[]`, `actions[]` (one English sentence per item — not the raw structure), `lastRun`
(`{at, ok}` or `null`) and `agent` (`null`, or who wrote it and when).

```json
{
  "count": 1,
  "automations": [
    {
      "id": "aksamlambalari",
      "name": "Akşam lambaları",
      "enabled": true,
      "triggers": ["at sunset-15m every day"],
      "conditions": [],
      "actions": ["set `state` on `0x00158d0007a1b2c3` (Salon lambası) to \"ON\""],
      "lastRun": { "at": "2026-08-07T17:42:03.000Z", "ok": true },
      "agent": null
    }
  ]
}
```

### `get_automation` — the exact structure of one rule

**Input:** `id`. **Output:** the rule in exactly the shape `write_automation` accepts. Read, edit,
send the whole body back — an update **replaces** the rule, it does not merge.

```json
{
  "id": "aksamlambalari",
  "name": "Akşam lambaları",
  "enabled": true,
  "triggers": [{ "type": "sun", "event": "sunset", "offsetMinutes": -15, "days": [1, 2, 3, 4, 5, 6, 7] }],
  "conditions": [],
  "actions": [{ "type": "device", "deviceId": "0x00158d0007a1b2c3", "property": "state", "value": "ON" }],
  "lastRunAt": "2026-08-07T17:42:03.000Z",
  "lastRunOk": true,
  "agent": null
}
```

#### Triggers

| `type` | Fields | Meaning |
|---|---|---|
| `time` | `at` (`"HH:MM"`), `days[]` | At a clock time. `days`: 1 = Monday … 7 = Sunday |
| `sun` | `event` (`sunrise`\|`sunset`), `offsetMinutes` (−240..240), `days[]` | Relative to the sun; needs a home location to be set |
| `deviceAction` | `deviceId`, `action` (e.g. `"1_single"`) | A button press. Each button of a multi-button switch is its own trigger |
| `deviceState` | `deviceId`, `property`, and `equals` **or** `above`/`below`, optional `forSeconds` | On the **edge**: when the value crosses into the target. `forSeconds` (1..86400) means "and holds there". With none of `equals`/`above`/`below`, every change fires |

Note the field is `property` — the MQTT key (`state`, `state_l1`, `brightness`, `occupancy`), not
the `controls[].id` that `set_device` uses. `get_device` names channels; automation rules address
MQTT properties. If you are unsure, copy the `property` from an existing rule via `get_automation`.

#### Conditions (default: all must hold; set `conditionMode: "any"` for or)

| `type` | Fields |
|---|---|
| `timeRange` | `from`, `to`, each `{"kind":"clock","at":"22:00"}` or `{"kind":"sun","event":"sunset","offsetMinutes":0}`; optional `days[]`. May cross midnight |
| `deviceState` | `deviceId`, `property`, and **exactly one** of `equals`, `not`, or `above`/`below`; optional `forSeconds`. Reads the value *now*, not the edge |

#### Actions

| `type` | Fields |
|---|---|
| `device` | `deviceId`, `property`, `value` |
| `group` | `groupId` (e.g. `"group-7"`), `property`, `value` |
| `scene` | `groupId`, `sceneId` (0..255) |
| `delay` | `seconds` (1..300) |

Any action may carry `when: {equals}` — run only when the triggering event's value equals that.
A `device` action may also carry:

- `autoOff: {mode, seconds, value}` — undo itself. `after`: after `seconds` (1..86400). `idle`:
  when the triggering channel leaves its firing value, `seconds` being extra waiting (may be 0);
  `idle` requires a `deviceState` trigger that carries `equals`.
- `follow: {mode}` — use the triggering event's live value instead of `value`. `ratio` maps
  percentages between numeric channels, `copy` copies a colour. Requires a `deviceState` trigger
  with no target value (a plain "whenever it changes").

Limits: 64 rules, 8 triggers, 4 conditions, 8 actions, name 1..64 characters. A rule must have at
least one action that is not a `delay`.

### `write_automation` — create, update, delete

**Input:** `action` (`create` | `update` | `delete`), `id` (required for update and delete), and
`automation` (the body, for create and update). `id`, `lastRunAt`, `lastRunOk` and `agent` inside
the body are server-owned and ignored — the server assigns the id on create and keeps the run
history on update.

**Output:** `action`, `id`, `name` and `automation` (the saved rule, or `null` after a delete).

```json
{
  "name": "write_automation",
  "arguments": {
    "action": "create",
    "automation": {
      "name": "Hol gece ışığı",
      "triggers": [
        { "type": "deviceState", "deviceId": "0x00158d000a11cc42", "property": "occupancy", "equals": true }
      ],
      "conditions": [
        {
          "type": "timeRange",
          "from": { "kind": "sun", "event": "sunset", "offsetMinutes": 0 },
          "to": { "kind": "sun", "event": "sunrise", "offsetMinutes": 0 }
        }
      ],
      "actions": [
        {
          "type": "device",
          "deviceId": "0x00158d0007a1b2c3",
          "property": "brightness",
          "value": 60,
          "autoOff": { "mode": "idle", "seconds": 120, "value": 1 }
        }
      ]
    }
  }
}
```

```json
{
  "action": "create",
  "id": "1bce46ccfa07",
  "name": "Hol gece ışığı",
  "automation": {
    "id": "1bce46ccfa07",
    "name": "Hol gece ışığı",
    "enabled": true,
    "triggers": [{ "type": "deviceState", "deviceId": "0x00158d000a11cc42", "property": "occupancy", "equals": true }],
    "conditions": [{
      "type": "timeRange",
      "from": { "kind": "sun", "event": "sunset", "offsetMinutes": 0 },
      "to": { "kind": "sun", "event": "sunrise", "offsetMinutes": 0 }
    }],
    "actions": [{
      "type": "device",
      "deviceId": "0x00158d0007a1b2c3",
      "property": "brightness",
      "value": 60,
      "autoOff": { "mode": "idle", "seconds": 120, "value": 1 }
    }],
    "lastRunAt": null,
    "lastRunOk": null,
    "agent": { "tokenId": "PnGknZByVHVO", "tokenName": "Asistan", "at": "2026-08-08T22:19:01.342Z" }
  }
}
```

The `agent` stamp is added by the server, never by you. It records **which token** wrote the rule
and when — the token id and its human-given name, never the token value itself.

Deleting a rule that does not exist is an error, not a silent success. Validation errors come back
as `isError: true` with the server's own (Turkish) wording appended after an English lead-in:

```
The rule was rejected when saving; nothing was changed. Reason (server wording): Otomasyon eylemi değeri kumandanın aralığı dışında.
```

Read the reason and fix the body — the most common causes are a value outside the channel's
`min`/`max`, a lock or siren used as an action, and a rule triggered by a channel it writes.

### `control_automation` — enable, disable, run

**Input:** `id` and `action` (`enable` | `disable` | `run`).

**Output:** `id`, `name`, `action`, `enabled`, `changed`, and for `run` an `outcome`.

```json
{ "name": "control_automation", "arguments": { "id": "aksamlambalari", "action": "run" } }
```

```json
{ "id": "aksamlambalari", "name": "Akşam lambaları", "action": "run", "enabled": true, "changed": true, "outcome": "ok" }
```

`outcome` is `ok`, `skipped` (no action matched — a manual run has no triggering event, so actions
carrying `when` are skipped) or `blocked` (the rule's conditions are not met right now). `skipped`
and `blocked` are successful results, not failures. "Already running" and "the device command
failed" come back as `isError: true`.

`enable` and `disable` change the rule: they are backed up and stamped exactly like
`write_automation`. Asking for a state the rule is already in returns `changed: false` and writes
nothing.

---

## 4. Errors

Two shapes, and the difference matters:

**`isError: true` inside a successful `result`** — you did something the model can fix. Unknown
device, unknown channel, a value of the wrong type, a rejected rule, a lock or siren. Read the
message and try again differently. There is no `structuredContent` on these.

**A JSON-RPC `error` object** (HTTP 400/401/403/404/405) — the request itself is malformed and
retrying the same thing will not help. Fix the client, not the arguments.

| Code | Meaning |
|---|---|
| `-32600` | The body is not a single JSON-RPC object |
| `-32601` | Unknown method |
| `-32602` | Unknown tool name, or a required `_meta` field is missing or has the wrong type |
| `-32020` | A required header is missing or does not match the body |
| `-32022` | Unsupported protocol version (the `data.supported` array lists what the server takes) |
| `40100` / `40300` / `40500` | Transport: unauthorised / origin refused / method not allowed |

---

## 5. Safety rules

1. **Identity is the IEEE address.** Friendly names change; addresses do not. If the user says
   "the living room lamp", call `list_devices` first and map it. Never guess an address.
2. **Locks and sirens cannot be written by an agent.** `set_device` refuses them, and they can
   never be an automation action. The reason is not that the model is untrusted in particular:
   there is no human to approve the moment it happens. The panel is unaffected — the user can
   still unlock the door and silence the siren by hand. (An installation may lift this with
   `mcp.allowDangerousControls: true` in `config/default.yaml`; the default is `false` and should
   stay that way unless the user deliberately changes it.)
3. **`set_device` and `run` act on the real house.** No previews, no undo. If a request is
   ambiguous, ask before acting rather than picking a device.
4. **Writes to rules are recorded and reversible.** Before every agent write, the server copies
   `automations.json` aside (the last 20 copies are kept) and stamps the rule with the token id and
   name. In the panel the affected rules carry a small 🤖 **Assistant** chip, and a bar above the
   list offers **Undo assistant change**, which restores the most recent backup. Pressing it again
   steps back one more change. Reverting is the user's job, not the model's — there is no tool for
   it on purpose.
5. **A rule cannot be triggered by a channel it writes.** That is a loop and it is rejected at save
   time, not at run time.
6. **A person editing a rule in the panel clears the agent stamp.** The wizard rebuilds the rule
   from scratch; from then on the rule is theirs.

---

## 6. System prompt you can paste into your model

```text
You control a home through the Villa Bridge MCP server. Seven tools: list_devices, get_device,
set_device, list_automations, get_automation, write_automation, control_automation.

Devices are identified by their IEEE address (e.g. 0x00158d0007a1b2c3), never by name. When the
user names a device in words, call list_devices first and map the name to an address. Call
get_device before writing, so you know the channel ids (controls[].id) and their ranges.

set_device changes the real house immediately: there is no preview and no undo. Use it only for
what the user actually asked for, one channel at a time. Numbers are clamped to the channel's
min/max, so report the "applied" value back, not the one you requested. Colours are hex strings
like #ffaa00. On/off channels take true or false.

Locks and sirens cannot be operated through these tools and the attempt will be refused. Do not
retry it in another form; tell the user to do it themselves from the Villa Bridge panel.

For automations: list_automations to see what exists, get_automation to read one exactly,
write_automation to create/update/delete, control_automation to enable/disable/run. An update
replaces the whole rule, so read it first and send back the complete body. Rules you write take
effect immediately and act on their own afterwards, so describe what a new rule will do and get
the user's agreement before saving it. control_automation with "run" fires the rule's actions on
the real devices right now — use it when the user wants the rule to happen now, not to test it.

When a result comes back with isError: true, the message explains what to change; fix it and try
again. A JSON-RPC error means the request itself was malformed — do not retry it unchanged.

Prefer asking a short clarifying question over acting on a guess.
```
