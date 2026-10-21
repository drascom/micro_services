import { randomBytes } from "node:crypto";
import type { FastifyInstance, FastifyReply, FastifyRequest } from "fastify";
import type { Automation, AutomationAction, AutomationCondition, AutomationTrigger } from "./automations.js";
import { forbiddenAutomationControlKinds } from "./automations.js";
import type { AutomationRunResult } from "./automation-engine.js";
import { normalizeControlValue } from "./device-controls.js";
import { isHomeFavoriteControlKind } from "./home-favorites.js";
import type { HomeGroup } from "./home-groups.js";
import type { DeviceControlView, DeviceView, JsonObject } from "./types.js";

/**
 * Model Context Protocol ucu — sürüm **2026-07-28**, durumsuz.
 *
 * Bu sürümde oturum yoktur: `Mcp-Session-Id`, GET akışı, `initialize` el sıkışması ve
 * `Last-Event-ID` **kullanılmaz**. Tek yol `POST /mcp`; her istek kendi başına doğrulanır ve
 * yanıtı tek bir JSON nesnesidir. Resmî SDK bilerek kullanılmaz: `apps/runtime` kendi kilitli ve
 * daha eski bağımlılık setiyle çalışıyor, Android paketlemesi ondan besleniyor — yeni npm
 * bağımlılığı iki tarafta da yeniden paketleme demek. JSON-RPC elle yazılır, ayrı süreç yoktur.
 */

/** Uç adresi tek yerde: yetki kapısı (`access-control.ts`) da buradan okur. */
export const mcpRoutePath = "/mcp";

/** Desteklenen protokol sürümleri; yükseltme yalnız bu listeye dokunur. */
export const SUPPORTED_PROTOCOL_VERSIONS = ["2026-07-28"] as const;

/** `params._meta` içinde protokol sürümünün taşındığı anahtar. */
export const protocolVersionMetaKey = "io.modelcontextprotocol/protocolVersion";

/** `params._meta` içinde istemci yeteneklerinin taşındığı anahtar; sürümle birlikte zorunludur. */
export const clientCapabilitiesMetaKey = "io.modelcontextprotocol/clientCapabilities";

/** Sonucun `_meta` alanında sunucu kimliğinin döndüğü anahtar (spesifikasyon: SHOULD). */
export const serverInfoMetaKey = "io.modelcontextprotocol/serverInfo";

/**
 * Sunucu kimliği. Sürüm bilerek **sabit**: `package.json` çalışma anında okunsaydı yol çözümü
 * dist/ , tsx ve Android paketlemesi için üç ayrı davranış demek olurdu. Sürüm sürüklenmesini
 * `mcp.test.ts` içindeki `package.json` karşılaştırması yakalar.
 */
export const mcpServerInfo = { name: "villa-bridge", version: "0.1.0" } as const;

export const mcpErrorCodes = {
  /** Zorunlu başlık eksik ya da gövdeyle uyuşmuyor (HeaderMismatch). */
  headerMismatch: -32020,
  /** İstemcinin istediği protokol sürümü desteklenmiyor (UnsupportedProtocolVersion). */
  unsupportedProtocolVersion: -32022,
  /*
   * Aşağıdaki üçü spesifikasyonun tanımladığı bir durum değil, bu sunucunun taşıma katmanı
   * hataları. Spesifikasyon `-32000`..`-32019` aralığını "legacy" ilan etti ve yeni kodların
   * JSON-RPC'nin ayrılmış aralığı olan `-32768`..`-32000` **dışında** tahsis edilmesini istiyor.
   * Şema: kod = HTTP durum kodu × 100. Böylece kod okunur kalıyor (40100 → 401), ayrılmış
   * aralığın çok dışında ve ileride aynı HTTP durumunun alt sebeplerine yer bırakıyor.
   */
  /** Taşıma katmanı: token yok/geçersiz (HTTP 401). */
  unauthorized: 40100,
  /** Taşıma katmanı: `Origin` reddedildi (HTTP 403). */
  forbiddenOrigin: 40300,
  /** Taşıma katmanı: `POST` dışında bir yöntem (HTTP 405). */
  methodNotAllowed: 40500,
  invalidRequest: -32600,
  methodNotFound: -32601,
  invalidParams: -32602
} as const;

export interface McpErrorBody extends JsonObject {
  jsonrpc: "2.0";
  id: null;
  error: { code: number; message: string; data?: JsonObject };
}

/** Taşıma katmanı hataları da JSON-RPC hata nesnesiyle döner; `id` bilinmediği için `null`. */
export const mcpErrorBody = (code: number, message: string, data?: JsonObject): McpErrorBody => ({
  jsonrpc: "2.0",
  id: null,
  error: data === undefined ? { code, message } : { code, message, data }
});

/**
 * `Origin` doğrulaması (DNS rebinding). İzinli köken listesi yapılandırmadan gelir; boşsa
 * tarayıcı dışından gelen istemci (Origin göndermeyen) çalışır, Origin gönderen reddedilir.
 */
export const isAllowedMcpOrigin = (
  origin: string | undefined,
  allowedOrigins: readonly string[]
): boolean => (origin === undefined ? true : allowedOrigins.includes(origin));

/**
 * Başlık değeri `=?base64?...?=` (ya da RFC 2047 `=?utf-8?B?...?=`) sentinel biçimindeyse çözer.
 * ASCII dışı cihaz adları başlıkta bu şekilde taşınır; gövdeyle karşılaştırma çözülmüş değer
 * üzerinden yapılır.
 */
export const decodeHeaderSentinel = (value: string): string => {
  const match = /^=\?([^?]+)\?(?:([Bb])\?)?([A-Za-z0-9+/=_-]*)\?=$/.exec(value);
  if (!match) return value;
  const [, charset, encoding, data] = match;
  if (charset.toLowerCase() !== "base64" && encoding === undefined) return value;
  return Buffer.from(data, "base64").toString("utf8");
};

const headerValue = (
  headers: Record<string, string | string[] | undefined>,
  name: string
): string | undefined => {
  // Fastify başlık adlarını küçük harfe indirir; değerler duyarlı kalır.
  const value = headers[name];
  return Array.isArray(value) ? value[0] : value;
};

/**
 * İsteği doğrulayan ajan kaydının **gizli olmayan** kimliği. Kapı (`access-control.ts`) token'ı
 * çözer, buraya yalnız `id` + `name` gelir; ham token bu katmana hiç girmez ve hiçbir yere yazılmaz.
 */
export interface McpAgentIdentity {
  id: string;
  name: string;
}

/** Otomasyon araçlarının ihtiyacı olan üç iş; hepsi panelin kullandığı yolların aynısıdır. */
export interface McpAutomationDependencies {
  /** `AutomationsStore.get()`. */
  list: () => Promise<Automation[]>;
  /**
   * `AutomationsStore.save()` — doğrulama (kilit/siren, döngü koruması, sınırlar) burada olur.
   *
   * **Çağrı yeri yazmadan önce yedek almakla yükümlüdür** (`AutomationBackupStore.capture()`).
   * `mcp.ts` bilerek dosya sistemine hiç dokunmaz; yedeğin ajan yoluna özel olduğu bilgisi
   * `src/index.ts` wiring'inde tek yerde durur.
   */
  save: (automations: Automation[]) => Promise<Automation[]>;
  /** Elle çalıştırma — `POST /api/automations/:id/run` ile aynı motor yolu. */
  run: (id: string) => Promise<AutomationRunResult>;
}

export interface McpEndpointDependencies {
  devices: () => DeviceView[];
  homeGroups: () => Promise<HomeGroup[]>;
  /** Kumandaya yazma; HTTP rotasının kullandığı `ZigbeeSource.setDevice` yolunun aynısı. */
  setDevice: (deviceId: string, payload: JsonObject) => Promise<void>;
  automations: McpAutomationDependencies;
  /**
   * §8.1 muafiyeti (`config.mcp.allowDangerousControls`). Varsayılan **kapalı**: kilit ve siren
   * ajan yolundan yazılamaz. Kullanıcı bilerek açarsa bu bayrak `true` döner.
   */
  allowDangerousControls?: () => boolean;
}

export interface McpHttpResponse {
  status: number;
  body: JsonObject | null;
}

interface ToolResult {
  structuredContent?: JsonObject;
  isError: boolean;
  text: string;
}

/** Modelin düzeltebileceği hata: JSON-RPC hatası değil, `isError: true` olan başarılı sonuç. */
class ToolInputError extends Error {}

const deviceCategories = ["light", "switch", "cover", "lock", "climate", "fan", "unknown"];
const availabilityValues = ["online", "offline", "unknown"];

const controlValueSchema = {
  type: ["boolean", "number", "string", "null"]
};

const primaryChannelSchema = {
  type: ["object", "null"],
  properties: {
    id: { type: "string" },
    name: { type: "string" },
    kind: { type: "string" },
    value: controlValueSchema,
    unit: { type: "string" }
  },
  required: ["id", "name", "kind", "value"],
  additionalProperties: false
};

const controlSchema = {
  type: "object",
  properties: {
    id: { type: "string" },
    name: { type: "string" },
    kind: { type: "string" },
    value: controlValueSchema,
    min: { type: "number" },
    max: { type: "number" },
    step: { type: "number" },
    unit: { type: "string" },
    values: { type: "array", items: { type: ["string", "number", "boolean"] } },
    adminOnly: { type: "boolean" }
  },
  required: ["id", "name", "kind", "value"],
  additionalProperties: false
};

const listDevicesInputSchema = {
  type: "object",
  properties: {
    room: { type: "string", description: "Only devices whose room name contains this text (case-insensitive)." },
    category: { type: "string", enum: deviceCategories, description: "Only devices of this category." },
    onlineOnly: { type: "boolean", description: "Only devices that are currently reachable." },
    search: { type: "string", description: "Free text matched against the device name and address." }
  },
  additionalProperties: false
};

const listDevicesOutputSchema = {
  type: "object",
  properties: {
    count: { type: "integer" },
    devices: {
      type: "array",
      items: {
        type: "object",
        properties: {
          id: { type: "string", description: "Immutable IEEE address; the only identity accepted by other tools." },
          name: { type: "string" },
          room: { type: ["string", "null"] },
          category: { type: "string", enum: deviceCategories },
          availability: { type: "string", enum: availabilityValues },
          primary: primaryChannelSchema
        },
        required: ["id", "name", "room", "category", "availability", "primary"],
        additionalProperties: false
      }
    }
  },
  required: ["count", "devices"],
  additionalProperties: false
};

const getDeviceInputSchema = {
  type: "object",
  properties: {
    id: {
      type: "string",
      description: "IEEE address of the device, for example 0x00158d0007a1b2c3."
    }
  },
  required: ["id"],
  additionalProperties: false
};

const getDeviceOutputSchema = {
  type: "object",
  properties: {
    id: { type: "string" },
    name: { type: "string" },
    room: { type: ["string", "null"] },
    category: { type: "string", enum: deviceCategories },
    availability: { type: "string", enum: availabilityValues },
    lastSeen: { type: ["string", "null"] },
    linkquality: { type: "number" },
    powerSource: { type: "string" },
    controls: { type: "array", items: controlSchema }
  },
  required: ["id", "name", "room", "category", "availability", "lastSeen", "controls"],
  additionalProperties: false
};

const setDeviceInputSchema = {
  type: "object",
  properties: {
    id: { type: "string", description: "IEEE address of the device, as returned by `list_devices`." },
    control: {
      type: "string",
      description: "Channel id, copied verbatim from `controls[].id` of `get_device`."
    },
    value: {
      type: ["boolean", "number", "string"],
      description:
        "New value. Booleans for on/off channels, a number for level/temperature/position "
        + "channels, one of `values[]` for list channels, `#rrggbb` for colour channels."
    }
  },
  required: ["id", "control", "value"],
  additionalProperties: false
};

const setDeviceOutputSchema = {
  type: "object",
  properties: {
    id: { type: "string" },
    name: { type: "string" },
    control: { type: "string" },
    kind: { type: "string" },
    requested: controlValueSchema,
    applied: {
      type: ["boolean", "number", "string", "object"],
      description:
        "What was actually sent after clamping to `min`/`max`, snapping to `step` and converting "
        + "a hex colour to its xy coordinates. Compare it with `requested` to see any correction."
    }
  },
  required: ["id", "name", "control", "kind", "requested", "applied"],
  additionalProperties: false
};

const daysSchema = {
  type: "array",
  items: { type: "integer", minimum: 1, maximum: 7 },
  description: "Weekdays, 1 = Monday … 7 = Sunday."
};

const scalarSchema = { type: ["boolean", "number", "string"] };

const automationTriggerSchema = {
  type: "object",
  description:
    "One of four shapes, picked by `type`: `time` (at+days), `sun` (event+offsetMinutes+days), "
    + "`deviceAction` (a button press: deviceId+action), `deviceState` (deviceId+property plus "
    + "`equals`, or `above`/`below`, optionally `forSeconds`).",
  properties: {
    type: { type: "string", enum: ["time", "sun", "deviceAction", "deviceState"] },
    at: { type: "string", description: "`time` only: local clock, \"HH:MM\"." },
    days: daysSchema,
    event: { type: "string", enum: ["sunrise", "sunset"], description: "`sun` only." },
    offsetMinutes: { type: "integer", description: "`sun` only: minutes from the sun event; negative = before." },
    deviceId: { type: "string", description: "IEEE address; device triggers only." },
    action: { type: "string", description: "`deviceAction` only: the MQTT action value, e.g. \"1_single\"." },
    property: { type: "string", description: "`deviceState` only: MQTT property key, e.g. \"occupancy\"." },
    equals: scalarSchema,
    above: { type: "number" },
    below: { type: "number" },
    forSeconds: { type: "integer", description: "`deviceState` only: the target must hold this long (1..86400)." }
  },
  required: ["type"]
};

const automationConditionSchema = {
  type: "object",
  description:
    "`timeRange` (from/to, each `{kind:\"clock\",at}` or `{kind:\"sun\",event,offsetMinutes}`, may "
    + "cross midnight) or `deviceState` (deviceId+property plus exactly one of `equals`, `not` or "
    + "`above`/`below`, optionally `forSeconds` and `freshWithinSeconds`).",
  properties: {
    type: { type: "string", enum: ["timeRange", "deviceState"] },
    from: { type: "object" },
    to: { type: "object" },
    days: daysSchema,
    deviceId: { type: "string" },
    property: { type: "string" },
    equals: scalarSchema,
    not: scalarSchema,
    above: { type: "number" },
    below: { type: "number" },
    forSeconds: { type: "integer" },
    freshWithinSeconds: {
      type: "integer",
      description:
        "`deviceState` only: the value must have been reported within this many seconds (1..3600). "
        + "Not the same as `forSeconds`: this asks when the device last spoke, not how long the "
        + "value has held. Omit for no limit. Unknown after a restart, which fails the condition."
    }
  },
  required: ["type"]
};

const automationActionSchema = {
  type: "object",
  description:
    "`device` (deviceId+property+value), `group` (groupId+property+value), `scene` "
    + "(groupId+sceneId) or `delay` (seconds, 1..300). Locks and sirens can never be an action.",
  properties: {
    type: { type: "string", enum: ["device", "group", "scene", "delay"] },
    deviceId: { type: "string", description: "`device` only: IEEE address." },
    property: { type: "string", description: "MQTT property key written on the device or group." },
    controlId: { type: "string", description: "Optional, presentation only." },
    value: scalarSchema,
    groupId: { type: "string", description: "`group`/`scene` only, e.g. \"group-7\"." },
    sceneId: { type: "integer", minimum: 0, maximum: 255 },
    seconds: { type: "integer", description: "`delay` only, 1..300." },
    when: {
      type: "object",
      description: "Run this action only when the triggering event value equals `when.equals`.",
      properties: { equals: scalarSchema },
      required: ["equals"],
      additionalProperties: false
    },
    autoOff: {
      type: "object",
      description:
        "Undo the action later. `after` = after `seconds`; `idle` = when the triggering channel "
        + "leaves its firing value (`seconds` is then extra waiting and may be 0).",
      properties: {
        mode: { type: "string", enum: ["after", "idle"] },
        seconds: { type: "integer" },
        value: scalarSchema
      },
      required: ["mode", "seconds", "value"],
      additionalProperties: false
    },
    follow: {
      type: "object",
      description:
        "Use the triggering event's live value instead of `value`. `ratio` maps percentages "
        + "between numeric channels, `copy` copies a colour. Needs a `deviceState` trigger with "
        + "no target value.",
      properties: { mode: { type: "string", enum: ["ratio", "copy"] } },
      required: ["mode"],
      additionalProperties: false
    }
  },
  required: ["type"]
};

const automationAgentSchema = {
  type: ["object", "null"],
  description: "Set when an agent wrote the rule; `null` for rules a person wrote.",
  properties: {
    tokenId: { type: "string" },
    tokenName: { type: "string" },
    at: { type: "string" }
  },
  required: ["tokenId", "tokenName", "at"],
  additionalProperties: false
};

/** `get_automation` çıktısı ile `write_automation` girdisi aynı biçimdir — model okuduğunu geri yazar. */
const automationSchema = {
  type: "object",
  properties: {
    id: { type: "string", description: "Assigned by the server on create; never write it yourself." },
    name: { type: "string", description: "1..64 characters, shown to the user." },
    enabled: { type: "boolean" },
    manual: {
      type: "boolean",
      description: "True for a user-run scene. Manual scenes must have an empty triggers array."
    },
    triggers: { type: "array", maxItems: 8, items: automationTriggerSchema },
    conditions: { type: "array", maxItems: 4, items: automationConditionSchema },
    conditionMode: {
      type: "string",
      enum: ["all", "any"],
      description: "How the conditions combine. Omitted means `all`."
    },
    actions: { type: "array", minItems: 1, maxItems: 8, items: automationActionSchema },
    lastRunAt: { type: ["string", "null"] },
    lastRunOk: { type: ["boolean", "null"] },
    agent: automationAgentSchema
  },
  required: ["id", "name", "enabled", "triggers", "conditions", "actions", "lastRunAt", "lastRunOk"],
  additionalProperties: false
};

const listAutomationsInputSchema = {
  type: "object",
  properties: {
    enabledOnly: { type: "boolean", description: "Only rules that are currently switched on." },
    search: { type: "string", description: "Free text matched against the rule name." }
  },
  additionalProperties: false
};

const listAutomationsOutputSchema = {
  type: "object",
  properties: {
    count: { type: "integer" },
    automations: {
      type: "array",
      items: {
        type: "object",
        properties: {
          id: { type: "string", description: "Pass this to `get_automation`, `write_automation` and `control_automation`." },
          name: { type: "string" },
          enabled: { type: "boolean" },
          manual: { type: "boolean" },
          triggers: { type: "array", items: { type: "string" }, description: "Readable summary, one line per trigger." },
          conditions: { type: "array", items: { type: "string" } },
          actions: { type: "array", items: { type: "string" } },
          lastRun: {
            type: ["object", "null"],
            properties: { at: { type: "string" }, ok: { type: ["boolean", "null"] } },
            required: ["at", "ok"],
            additionalProperties: false
          },
          agent: automationAgentSchema
        },
        required: ["id", "name", "enabled", "manual", "triggers", "conditions", "actions", "lastRun", "agent"],
        additionalProperties: false
      }
    }
  },
  required: ["count", "automations"],
  additionalProperties: false
};

const getAutomationInputSchema = {
  type: "object",
  properties: { id: { type: "string", description: "Rule id from `list_automations`." } },
  required: ["id"],
  additionalProperties: false
};

const writeAutomationInputSchema = {
  type: "object",
  properties: {
    action: { type: "string", enum: ["create", "update", "delete"] },
    id: { type: "string", description: "Required for `update` and `delete`; ignored on `create`." },
    automation: {
      type: "object",
      description:
        "The rule body for `create` and `update`; same shape `get_automation` returns. `id`, "
        + "`lastRunAt`, `lastRunOk` and `agent` are server-owned and ignored here.",
      properties: {
        name: { type: "string" },
        enabled: { type: "boolean" },
        manual: {
          type: "boolean",
          description: "True for a user-run scene. Manual scenes must have an empty triggers array."
        },
        triggers: { type: "array", maxItems: 8, items: automationTriggerSchema },
        conditions: { type: "array", maxItems: 4, items: automationConditionSchema },
        conditionMode: { type: "string", enum: ["all", "any"] },
        actions: { type: "array", minItems: 1, maxItems: 8, items: automationActionSchema }
      },
      required: ["name", "triggers", "actions"]
    }
  },
  required: ["action"],
  additionalProperties: false
};

const writeAutomationOutputSchema = {
  type: "object",
  properties: {
    action: { type: "string", enum: ["create", "update", "delete"] },
    id: { type: "string" },
    name: { type: "string" },
    automation: { ...automationSchema, type: ["object", "null"] }
  },
  required: ["action", "id", "name", "automation"],
  additionalProperties: false
};

const controlAutomationInputSchema = {
  type: "object",
  properties: {
    id: { type: "string", description: "Rule id from `list_automations`." },
    action: {
      type: "string",
      enum: ["enable", "disable", "run"],
      description: "`run` executes the rule's actions on the real devices, right now."
    }
  },
  required: ["id", "action"],
  additionalProperties: false
};

const controlAutomationOutputSchema = {
  type: "object",
  properties: {
    id: { type: "string" },
    name: { type: "string" },
    action: { type: "string", enum: ["enable", "disable", "run"] },
    enabled: { type: "boolean" },
    changed: { type: "boolean", description: "False when the rule was already in that state." },
    outcome: {
      type: "string",
      description: "`run` only: `ok`, `skipped` (no action matched) or `blocked` (conditions not met)."
    }
  },
  required: ["id", "name", "action", "enabled", "changed"],
  additionalProperties: false
};

/**
 * Araç kataloğu. Sıra **belirlenimcidir**: istemci önbelleği ve modelin prompt önbelleği aynı
 * baytları görsün diye liste sabit tutulur. Açıklamalar modelin okuyacağı metindir, İngilizce
 * yazılır; sunucunun günlük/hata metinleri projenin kuralı gereği Türkçe kalır.
 */
export const mcpTools = [
  {
    name: "list_devices",
    title: "List home devices",
    description:
      "List the devices of the home with their room, category, reachability and the value of "
      + "their main channel. Use this first when the user names a device in words: the returned "
      + "`id` (IEEE address) is the only identity the other tools accept. Raw device state is "
      + "deliberately not exposed; ask for a single device when you need every channel.",
    inputSchema: listDevicesInputSchema,
    outputSchema: listDevicesOutputSchema
  },
  {
    name: "get_device",
    title: "Get one device",
    description:
      "Return one device with all of its controllable channels (name, kind, current value, "
      + "range and unit). The `id` argument must be the IEEE address, never a friendly name: if "
      + "the user gave a name, call `list_devices` first and map it to an address.",
    inputSchema: getDeviceInputSchema,
    outputSchema: getDeviceOutputSchema
  },
  {
    name: "set_device",
    title: "Change one device channel",
    description:
      "Write a new value to one channel of one device — turn a light on, dim it, set a colour, "
      + "move a blind. **This acts on the real house immediately.** Read the channel with "
      + "`get_device` first and pass its `controls[].id` as `control`; the value is clamped to the "
      + "channel's `min`/`max` and snapped to its `step`, so the applied value can differ from the "
      + "requested one. Locks and sirens are refused: only a person may operate those, from the "
      + "Villa Bridge panel.",
    inputSchema: setDeviceInputSchema,
    outputSchema: setDeviceOutputSchema
  },
  {
    name: "list_automations",
    title: "List automation rules",
    description:
      "List the automation rules of the home with a readable summary of what triggers them and "
      + "what they do, plus how their last run went. Use it to answer \"what rules do I have\" and "
      + "to find the `id` the other automation tools need. Call `get_automation` when you need the "
      + "exact structure in order to change a rule.",
    inputSchema: listAutomationsInputSchema,
    outputSchema: listAutomationsOutputSchema
  },
  {
    name: "get_automation",
    title: "Get one automation rule",
    description:
      "Return one rule in full, in exactly the shape `write_automation` expects. To modify a rule, "
      + "read it with this tool, change the fields you mean to change, and send the whole body "
      + "back with `write_automation` — an update replaces the rule, it does not merge.",
    inputSchema: getAutomationInputSchema,
    outputSchema: automationSchema
  },
  {
    name: "write_automation",
    title: "Create, update or delete an automation rule",
    description:
      "Create, replace or delete one automation rule. The rule takes effect as soon as it is "
      + "saved, so it will act on the real house on its own from then on. The server backs up the "
      + "rule file before every write and marks the rule as agent-written, so the user can see "
      + "which rules you touched and undo them from the panel. Devices are addressed by IEEE "
      + "address only. A rule may not be triggered by a channel it writes (that would loop), and "
      + "locks and sirens may never be an action.",
    inputSchema: writeAutomationInputSchema,
    outputSchema: writeAutomationOutputSchema
  },
  {
    name: "control_automation",
    title: "Enable, disable or run an automation rule",
    description:
      "Switch a rule on or off, or run it now. **`run` executes the rule's actions on the real "
      + "devices immediately** — lights change, blinds move; use it only when the user asked for "
      + "the rule to happen right now, not to test one. `enable`/`disable` change the rule and are "
      + "recorded the same way `write_automation` is.",
    inputSchema: controlAutomationInputSchema,
    outputSchema: controlAutomationOutputSchema
  }
] as const;

/**
 * Ana kanal seçimi. Panelin kart mantığıyla aynı önceliği izler: ev ekranı döşemesi cihazın
 * aç/kapa kanalını çizer (kullanıcının cihaza dair ilk sorusu "açık mı" olduğu için), o yoksa
 * pano kanalı sayılan diğer kumandalara düşer, o da yoksa cihazın ilk kumandasına. Hiç kumanda
 * olmayan (salt sensör) cihazda `null` döner — uydurulmuş bir özet yerine "özeti yok" demek daha
 * dürüst, ayrıntıyı `get_device` verir.
 */
export const primaryControl = (controls: DeviceControlView[]): DeviceControlView | null =>
  controls.find((control) => control.kind === "switch")
  ?? controls.find((control) => isHomeFavoriteControlKind(control.kind))
  ?? controls[0]
  ?? null;

const primaryChannelView = (control: DeviceControlView | null): JsonObject | null =>
  control === null
    ? null
    : {
      id: control.id,
      name: control.name,
      kind: control.kind,
      value: control.value,
      ...(control.unit === undefined ? {} : { unit: control.unit })
    };

const controlDetailView = (control: DeviceControlView): JsonObject => ({
  id: control.id,
  name: control.name,
  kind: control.kind,
  value: control.value,
  ...(control.min === undefined ? {} : { min: control.min }),
  ...(control.max === undefined ? {} : { max: control.max }),
  ...(control.step === undefined ? {} : { step: control.step }),
  ...(control.unit === undefined ? {} : { unit: control.unit }),
  ...(control.values === undefined ? {} : { values: control.values }),
  ...(control.adminOnly === true ? { adminOnly: true } : {})
});

/**
 * Cihaz → oda adı. Oda kavramının kodda karşılığı ana ekran grubudur (`home-groups.json`).
 * Bir cihaz birden çok kartta görünebilir; ilk kart kazanır, çünkü panelde de kartlar yukarıdan
 * aşağı çizilir ve kullanıcının gördüğü ilk oda odur.
 */
const roomLookup = (groups: HomeGroup[]): Map<string, string> => {
  const rooms = new Map<string, string>();
  for (const group of groups) {
    for (const item of group.items) {
      if (!rooms.has(item.deviceId)) rooms.set(item.deviceId, group.name);
    }
  }
  return rooms;
};

const optionalString = (value: unknown, field: string): string | undefined => {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== "string") throw new ToolInputError(`\`${field}\` must be a string.`);
  return value;
};

const toolArguments = (value: unknown): JsonObject => {
  if (value === undefined || value === null) return {};
  if (typeof value !== "object" || Array.isArray(value)) {
    throw new ToolInputError("`arguments` must be an object.");
  }
  return value as JsonObject;
};

const listDevices = (
  argumentsValue: unknown,
  devices: DeviceView[],
  rooms: Map<string, string>
): JsonObject => {
  const input = toolArguments(argumentsValue);
  const room = optionalString(input.room, "room")?.trim().toLowerCase();
  const category = optionalString(input.category, "category")?.trim().toLowerCase();
  const search = optionalString(input.search, "search")?.trim().toLowerCase();
  if (input.onlineOnly !== undefined && typeof input.onlineOnly !== "boolean") {
    throw new ToolInputError("`onlineOnly` must be a boolean.");
  }
  if (category !== undefined && !deviceCategories.includes(category)) {
    throw new ToolInputError(
      `Unknown category \`${category}\`. Known categories: ${deviceCategories.join(", ")}.`
    );
  }
  const rows = devices
    .filter((device) => {
      const deviceRoom = rooms.get(device.id) ?? null;
      if (room !== undefined && !(deviceRoom ?? "").toLowerCase().includes(room)) return false;
      if (category !== undefined && device.category !== category) return false;
      if (input.onlineOnly === true && device.availability !== "online") return false;
      if (
        search !== undefined
        && !device.name.toLowerCase().includes(search)
        && !device.id.toLowerCase().includes(search)
      ) return false;
      return true;
    })
    .map((device) => ({
      id: device.id,
      name: device.name,
      room: rooms.get(device.id) ?? null,
      category: device.category,
      availability: device.availability,
      primary: primaryChannelView(primaryControl(device.controls))
    }));
  return { count: rows.length, devices: rows };
};

const getDevice = (
  argumentsValue: unknown,
  devices: DeviceView[],
  rooms: Map<string, string>
): JsonObject => {
  const input = toolArguments(argumentsValue);
  const id = optionalString(input.id, "id")?.trim().toLowerCase();
  if (!id) throw new ToolInputError("`id` is required and must be the IEEE address of a device.");
  const device = devices.find((candidate) => candidate.id === id);
  if (!device) {
    throw new ToolInputError(
      `No device with address \`${id}\`. Call \`list_devices\` and use the \`id\` field from `
      + "there; friendly names are not accepted."
    );
  }
  return {
    id: device.id,
    name: device.name,
    room: rooms.get(device.id) ?? null,
    category: device.category,
    availability: device.availability,
    lastSeen: device.lastSeen,
    ...(device.linkquality === undefined ? {} : { linkquality: device.linkquality }),
    ...(device.powerSource === undefined ? {} : { powerSource: device.powerSource }),
    controls: device.controls.map(controlDetailView)
  };
};

/**
 * Değer reddedildiğinde modele ne göndereceğimiz. `normalizeControlValue` panelin hata metinlerini
 * (Türkçe) fırlatır; bunlar kullanıcı arayüzü metinleridir ve modele gitmez. Onun yerine kumandanın
 * kendi tanım verisinden **düzeltilebilir** bir ipucu üretilir: model neyi göndereceğini okur.
 */
const controlValueHint = (control: DeviceControlView): string => {
  if (control.kind === "color") return "Send a hex colour string such as `#ffaa00`.";
  if (control.kind === "switch" || control.kind === "fan" || control.kind === "siren") {
    return "Send `true` to switch it on or `false` to switch it off.";
  }
  if (control.values?.length) {
    return `Send one of: ${control.values.map((value) => `\`${String(value)}\``).join(", ")}.`;
  }
  if (control.min !== undefined || control.max !== undefined) {
    const range = `between ${control.min ?? "any"} and ${control.max ?? "any"}`;
    return `Send a number ${range}${control.step === undefined ? "" : ` in steps of ${control.step}`}.`;
  }
  return "Send a number.";
};

/**
 * §8.1 — kilit ve siren bir **otomasyon eylemi** olamaz, çünkü otomasyonu onaylayacak bir insan
 * yoktur. Bir LLM de otomatik bir aktördür; gerekçe birebir geçerlidir, o yüzden yasak listesi de
 * birebir aynıdır (`forbiddenAutomationControlKinds`). Panelden elle kumanda etkilenmez: orada
 * düğmeye basan insanın kendisidir.
 */
const isAgentForbiddenControl = (
  control: DeviceControlView,
  dependencies: McpEndpointDependencies
): boolean =>
  forbiddenAutomationControlKinds.has(control.kind) && dependencies.allowDangerousControls?.() !== true;

const setDeviceTool = async (
  argumentsValue: unknown,
  devices: DeviceView[],
  dependencies: McpEndpointDependencies
): Promise<JsonObject> => {
  const input = toolArguments(argumentsValue);
  const id = optionalString(input.id, "id")?.trim().toLowerCase();
  if (!id) throw new ToolInputError("`id` is required and must be the IEEE address of a device.");
  const device = devices.find((candidate) => candidate.id === id);
  if (!device) {
    throw new ToolInputError(
      `No device with address \`${id}\`. Call \`list_devices\` and use the \`id\` field from `
      + "there; friendly names are not accepted."
    );
  }
  const controlId = optionalString(input.control, "control")?.trim().toLowerCase();
  if (!controlId) {
    throw new ToolInputError("`control` is required; it is the `id` of one of the device's controls.");
  }
  const control = device.controls.find((candidate) => candidate.id === controlId);
  if (!control) {
    const known = device.controls.map((item) => `\`${item.id}\` (${item.kind})`).join(", ");
    throw new ToolInputError(
      `Device \`${id}\` has no control \`${controlId}\`. `
      + (known ? `Its controls are: ${known}.` : "It exposes no writable control at all.")
    );
  }
  if (device.preparing) {
    throw new ToolInputError(
      `Device \`${id}\` is still being set up and does not accept commands yet. Try again shortly.`
    );
  }
  if (isAgentForbiddenControl(control, dependencies)) {
    throw new ToolInputError(
      `Control \`${controlId}\` is a ${control.kind} and cannot be written through the agent `
      + "endpoint. Locks and sirens are only operated by a person, from the Villa Bridge panel. "
      + "Do not retry; tell the user to do it themselves."
    );
  }
  if (input.value === undefined || input.value === null) {
    throw new ToolInputError(`\`value\` is required. ${controlValueHint(control)}`);
  }
  // Dönüşüm/doğrulama panelin ve otomasyon motorunun paylaştığı **tek** yoldan geçer: kırpma,
  // adım yuvarlaması ve hex→xy orada yaşar, burada kopyalanmaz.
  let applied: unknown;
  try {
    applied = normalizeControlValue(control, input.value);
  } catch {
    throw new ToolInputError(
      `\`value\` is not valid for control \`${controlId}\` (${control.kind}). ${controlValueHint(control)}`
    );
  }
  await dependencies.setDevice(id, { [control.property]: applied } as JsonObject);
  return {
    id,
    name: device.name,
    control: control.id,
    kind: control.kind,
    requested: input.value,
    applied: applied as JsonObject[string]
  };
};

const weekdayNames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const dayText = (days: number[] | undefined): string =>
  days === undefined || days.length === 7 || days.length === 0
    ? "every day"
    : `on ${days.map((day) => weekdayNames[day - 1] ?? String(day)).join(", ")}`;

const offsetText = (offsetMinutes: number): string =>
  offsetMinutes === 0 ? "" : `${offsetMinutes > 0 ? "+" : "-"}${Math.abs(offsetMinutes)}m`;

const scalarText = (value: unknown): string => JSON.stringify(value) ?? String(value);

/**
 * Okunur özet. `list_automations` ham iç yapıyı dökmez: model "hangi kurallar var" sorusunu tek
 * bakışta yanıtlasın, ham şekli gerçekten değiştirecekse `get_automation`'dan alsın.
 */
const triggerText = (trigger: AutomationTrigger, label: (id: string) => string): string => {
  if (trigger.type === "time") return `at ${trigger.at} ${dayText(trigger.days)}`;
  if (trigger.type === "sun") {
    return `at ${trigger.event}${offsetText(trigger.offsetMinutes)} ${dayText(trigger.days)}`;
  }
  if (trigger.type === "deviceAction") {
    return `when button \`${trigger.action}\` on ${label(trigger.deviceId)} is used`;
  }
  const target = trigger.equals !== undefined
    ? `becomes ${scalarText(trigger.equals)}`
    : trigger.above !== undefined && trigger.below !== undefined
      ? `enters the range ${trigger.above}..${trigger.below}`
      : trigger.above !== undefined
        ? `rises above ${trigger.above}`
        : trigger.below !== undefined ? `falls below ${trigger.below}` : "changes";
  const held = trigger.forSeconds === undefined ? "" : ` and stays there for ${trigger.forSeconds}s`;
  return `when \`${trigger.property}\` on ${label(trigger.deviceId)} ${target}${held}`;
};

const conditionText = (condition: AutomationCondition, label: (id: string) => string): string => {
  if (condition.type === "timeRange") {
    const point = (value: typeof condition.from): string =>
      value.kind === "clock" ? value.at : `${value.event}${offsetText(value.offsetMinutes)}`;
    const days = condition.days === undefined ? "" : ` ${dayText(condition.days)}`;
    return `between ${point(condition.from)} and ${point(condition.to)}${days}`;
  }
  const test = condition.equals !== undefined
    ? `is ${scalarText(condition.equals)}`
    : condition.not !== undefined
      ? `is not ${scalarText(condition.not)}`
      : condition.above !== undefined && condition.below !== undefined
        ? `is between ${condition.above} and ${condition.below}`
        : condition.above !== undefined
          ? `is above ${condition.above}`
          : `is below ${condition.below}`;
  const held = condition.forSeconds === undefined ? "" : ` for ${condition.forSeconds}s`;
  return `\`${condition.property}\` on ${label(condition.deviceId)} ${test}${held}`;
};

const actionText = (action: AutomationAction, label: (id: string) => string): string => {
  const only = action.when === undefined ? "" : `, only when the event value is ${scalarText(action.when.equals)}`;
  if (action.type === "delay") return `wait ${action.seconds}s${only}`;
  if (action.type === "scene") return `recall scene ${action.sceneId} of group \`${action.groupId}\`${only}`;
  if (action.type === "group") {
    return `set \`${action.property}\` on group \`${action.groupId}\` to ${scalarText(action.value)}${only}`;
  }
  const source = action.follow === undefined
    ? scalarText(action.value)
    : `the triggering value (${action.follow.mode})`;
  const undo = action.autoOff === undefined
    ? ""
    : action.autoOff.mode === "after"
      ? `, then set it to ${scalarText(action.autoOff.value)} after ${action.autoOff.seconds}s`
      : `, then set it to ${scalarText(action.autoOff.value)} once the trigger clears`;
  return `set \`${action.property}\` on ${label(action.deviceId)} to ${source}${undo}${only}`;
};

const agentView = (automation: Automation): JsonObject | null =>
  automation.agent === undefined ? null : { ...automation.agent };

const automationView = (automation: Automation): JsonObject => ({
  id: automation.id,
  name: automation.name,
  enabled: automation.enabled,
  ...(automation.manual === true ? { manual: true } : {}),
  triggers: automation.triggers as unknown as JsonObject[],
  conditions: automation.conditions as unknown as JsonObject[],
  ...(automation.conditionMode === undefined ? {} : { conditionMode: automation.conditionMode }),
  actions: automation.actions as unknown as JsonObject[],
  lastRunAt: automation.lastRunAt ?? null,
  lastRunOk: automation.lastRunOk ?? null,
  agent: agentView(automation)
});

const deviceLabeller = (devices: DeviceView[]) => (deviceId: string): string => {
  const device = devices.find((candidate) => candidate.id === deviceId);
  return device ? `\`${deviceId}\` (${device.name})` : `\`${deviceId}\``;
};

const listAutomations = (
  argumentsValue: unknown,
  automations: Automation[],
  devices: DeviceView[]
): JsonObject => {
  const input = toolArguments(argumentsValue);
  if (input.enabledOnly !== undefined && typeof input.enabledOnly !== "boolean") {
    throw new ToolInputError("`enabledOnly` must be a boolean.");
  }
  const search = optionalString(input.search, "search")?.trim().toLowerCase();
  const label = deviceLabeller(devices);
  const rows = automations
    .filter((automation) => {
      if (input.enabledOnly === true && !automation.enabled) return false;
      if (search !== undefined && !automation.name.toLowerCase().includes(search)) return false;
      return true;
    })
    .map((automation) => ({
      id: automation.id,
      name: automation.name,
      enabled: automation.enabled,
      manual: automation.manual === true,
      triggers: automation.triggers.map((trigger) => triggerText(trigger, label)),
      conditions: automation.conditions.map((condition) => conditionText(condition, label)),
      actions: automation.actions.map((action) => actionText(action, label)),
      lastRun: automation.lastRunAt
        ? { at: automation.lastRunAt, ok: automation.lastRunOk ?? null }
        : null,
      agent: agentView(automation)
    }));
  return { count: rows.length, automations: rows };
};

const findAutomation = (automations: Automation[], value: unknown): Automation => {
  const id = optionalString(value, "id")?.trim().toLowerCase();
  if (!id) throw new ToolInputError("`id` is required; take it from `list_automations`.");
  const automation = automations.find((candidate) => candidate.id === id);
  if (!automation) {
    throw new ToolInputError(
      `No automation with id \`${id}\`. Call \`list_automations\` for the current rules.`
    );
  }
  return automation;
};

/**
 * Kaydetme doğrulaması `AutomationsStore.save()` içindedir ve hata metinleri Türkçedir — panelin
 * kullanıcısına yazılmışlardır. Modele giden cümle İngilizce yazılır; altındaki asıl sebep olduğu
 * gibi eklenir, çünkü hangi alanın reddedildiğini yalnız o söyler.
 */
const saveAutomations = async (
  dependencies: McpEndpointDependencies,
  automations: Automation[]
): Promise<Automation[]> => {
  try {
    return await dependencies.automations.save(automations);
  } catch (error) {
    throw new ToolInputError(
      "The rule was rejected when saving; nothing was changed. Reason (server wording): "
      + (error instanceof Error ? error.message : String(error))
    );
  }
};

/** Yeni kural kimliği — `automationIdPattern` (8..32, küçük harf + rakam + tire) içinde kalır. */
const newAutomationId = (): string => randomBytes(6).toString("hex");

const writeAutomation = async (
  argumentsValue: unknown,
  automations: Automation[],
  dependencies: McpEndpointDependencies,
  agent: McpAgentIdentity | null,
  now: Date
): Promise<JsonObject> => {
  const input = toolArguments(argumentsValue);
  const action = optionalString(input.action, "action")?.trim().toLowerCase();
  if (action !== "create" && action !== "update" && action !== "delete") {
    throw new ToolInputError("`action` must be one of `create`, `update` or `delete`.");
  }
  if (action === "delete") {
    const existing = findAutomation(automations, input.id);
    await saveAutomations(dependencies, automations.filter((entry) => entry.id !== existing.id));
    return { action, id: existing.id, name: existing.name, automation: null };
  }
  const body = input.automation;
  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    throw new ToolInputError("`automation` must be an object holding the rule body.");
  }
  const previous = action === "update" ? findAutomation(automations, input.id) : null;
  // Damga: kimin yazdığı kuralın üstünde durur. Ham token buraya **hiç girmez** — yalnız kaydın
  // kimliği ve adı taşınır (bkz. `AutomationAgentStamp`).
  const stamp = agent === null
    ? {}
    : { agent: { tokenId: agent.id, tokenName: agent.name, at: now.toISOString() } };
  const candidate = {
    ...(body as JsonObject),
    id: previous?.id ?? newAutomationId(),
    // Çalışma geçmişi kuralın değil sunucunun bilgisidir: ayrı durum dosyasında durur, kural
    // gövdesiyle taşınmaz. Güncelleme onu sıfırlamaz çünkü tanıma hiç dokunmaz.
    ...stamp
  };
  const next = previous === null
    ? [...automations, candidate]
    : automations.map((entry) => (entry.id === previous.id ? candidate : entry));
  const saved = await saveAutomations(dependencies, next as unknown as Automation[]);
  const stored = saved.find((entry) => entry.id === candidate.id);
  if (!stored) throw new ToolInputError("The rule was not found after saving; nothing was changed.");
  return { action, id: stored.id, name: stored.name, automation: automationView(stored) };
};

const runOutcomeMessages: Record<string, string> = {
  busy: "The rule is already running. Wait a moment and try again.",
  failed: "The rule could not be run; the device command failed. The reason is in the run log.",
  missing: "The rule disappeared between listing and running it."
};

const controlAutomation = async (
  argumentsValue: unknown,
  automations: Automation[],
  dependencies: McpEndpointDependencies,
  agent: McpAgentIdentity | null,
  now: Date
): Promise<JsonObject> => {
  const input = toolArguments(argumentsValue);
  const action = optionalString(input.action, "action")?.trim().toLowerCase();
  if (action !== "enable" && action !== "disable" && action !== "run") {
    throw new ToolInputError("`action` must be one of `enable`, `disable` or `run`.");
  }
  const automation = findAutomation(automations, input.id);
  if (action === "run") {
    const outcome = await dependencies.automations.run(automation.id);
    if (outcome === "busy" || outcome === "failed" || outcome === "missing") {
      throw new ToolInputError(runOutcomeMessages[outcome]);
    }
    return {
      id: automation.id,
      name: automation.name,
      action,
      enabled: automation.enabled,
      changed: outcome === "ok",
      outcome
    };
  }
  const enabled = action === "enable";
  if (automation.enabled === enabled) {
    return { id: automation.id, name: automation.name, action, enabled, changed: false };
  }
  // Aç/kapa da kuralın değişmesidir: yedeği ve damgası yazma ile aynıdır.
  const stamp = agent === null
    ? {}
    : { agent: { tokenId: agent.id, tokenName: agent.name, at: now.toISOString() } };
  await saveAutomations(dependencies, automations.map((entry) => (entry.id === automation.id
    ? { ...entry, enabled, ...stamp }
    : entry)));
  return { id: automation.id, name: automation.name, action, enabled, changed: true };
};

/** Araçların çoğu cihaz listesini, otomasyon araçları kural listesini ister; ikisi de tembel okunur. */
const callTool = async (
  name: string,
  argumentsValue: unknown,
  dependencies: McpEndpointDependencies,
  agent: McpAgentIdentity | null
): Promise<ToolResult> => {
  try {
    const structuredContent = await (async (): Promise<JsonObject> => {
      if (name === "list_devices" || name === "get_device") {
        const rooms = roomLookup(await dependencies.homeGroups());
        const devices = dependencies.devices();
        return name === "list_devices"
          ? listDevices(argumentsValue, devices, rooms)
          : getDevice(argumentsValue, devices, rooms);
      }
      if (name === "set_device") {
        return setDeviceTool(argumentsValue, dependencies.devices(), dependencies);
      }
      const automations = await dependencies.automations.list();
      if (name === "list_automations") {
        return listAutomations(argumentsValue, automations, dependencies.devices());
      }
      if (name === "get_automation") {
        return automationView(findAutomation(automations, toolArguments(argumentsValue).id));
      }
      if (name === "write_automation") {
        return writeAutomation(argumentsValue, automations, dependencies, agent, new Date());
      }
      return controlAutomation(argumentsValue, automations, dependencies, agent, new Date());
    })();
    // Geriye uyum deseni: yapısal sonucun aynı JSON'u metin olarak da taşınır.
    return { structuredContent, isError: false, text: JSON.stringify(structuredContent) };
  } catch (error) {
    if (error instanceof ToolInputError) return { isError: true, text: error.message };
    throw error;
  }
};

export interface McpRequestInput {
  method: string;
  headers: Record<string, string | string[] | undefined>;
  body: unknown;
  /** Doğrulanmış ajan kimliği; otomasyon damgası buradan yazılır. */
  agent?: McpAgentIdentity | null;
}

const jsonRpcError = (
  id: unknown,
  code: number,
  message: string,
  data?: JsonObject
): JsonObject => ({
  jsonrpc: "2.0",
  id: id ?? null,
  error: data === undefined ? { code, message } : { code, message, data }
});

const unsupportedProtocolVersion = (id: unknown, requested: string | undefined): McpHttpResponse => ({
  status: 400,
  body: jsonRpcError(
    id,
    mcpErrorCodes.unsupportedProtocolVersion,
    "UnsupportedProtocolVersionError",
    {
      error: "UnsupportedProtocolVersionError",
      supported: [...SUPPORTED_PROTOCOL_VERSIONS],
      ...(requested === undefined ? {} : { requested })
    }
  )
});

const headerMismatch = (id: unknown, message: string): McpHttpResponse => ({
  status: 400,
  body: jsonRpcError(id, mcpErrorCodes.headerMismatch, message)
});

/**
 * Gövdedeki zorunlu alan eksik. `-32020` değil `-32602`: `HeaderMismatch` adı üstünde başlık
 * hatasıdır; başlıkta karşılığı olmayan bir `_meta` alanının eksikliği düpedüz geçersiz parametre.
 */
const invalidParams = (id: unknown, message: string): McpHttpResponse => ({
  status: 400,
  body: jsonRpcError(id, mcpErrorCodes.invalidParams, message)
});

/** Her sonuç sunucu kimliğini taşır (spesifikasyon: SHOULD). */
const resultMeta = (): JsonObject => ({
  [serverInfoMetaKey]: { name: mcpServerInfo.name, version: mcpServerInfo.version }
});

/**
 * Tek bir JSON-RPC isteğini (ya da bildirimini) işler. Fastify'dan bağımsız tutulur ki
 * doğrulama testleri HTTP sunucusu ayağa kaldırmadan da aynı yolu yürüsün.
 */
export const handleMcpRequest = async (
  request: McpRequestInput,
  dependencies: McpEndpointDependencies
): Promise<McpHttpResponse> => {
  if (request.method !== "POST") {
    return {
      status: 405,
      body: mcpErrorBody(
        mcpErrorCodes.methodNotAllowed,
        "MCP ucu yalnız POST kabul eder; bu sürümde GET akışı yoktur."
      )
    };
  }
  const body = request.body;
  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    return {
      status: 400,
      body: jsonRpcError(null, mcpErrorCodes.invalidRequest, "Request body must be a single JSON-RPC object.")
    };
  }
  const message = body as JsonObject;
  const id = message.id;
  const isNotification = id === undefined || id === null;
  if (message.jsonrpc !== "2.0" || typeof message.method !== "string") {
    return {
      status: 400,
      body: jsonRpcError(id, mcpErrorCodes.invalidRequest, "`jsonrpc` must be \"2.0\" and `method` a string.")
    };
  }
  const method = message.method;
  const parameters = typeof message.params === "object" && message.params !== null && !Array.isArray(message.params)
    ? message.params as JsonObject
    : {};
  const meta = typeof parameters._meta === "object" && parameters._meta !== null && !Array.isArray(parameters._meta)
    ? parameters._meta as JsonObject
    : {};
  const bodyVersion = typeof meta[protocolVersionMetaKey] === "string"
    ? meta[protocolVersionMetaKey] as string
    : undefined;
  const capabilities = meta[clientCapabilitiesMetaKey];
  const headerVersion = headerValue(request.headers, "mcp-protocol-version");

  // Eski çağın el sıkışması bu sürümde yoktur; istemciye desteklenen sürüm bildirilir.
  if (method === "initialize") return unsupportedProtocolVersion(id, headerVersion ?? bodyVersion);

  const headerMethod = headerValue(request.headers, "mcp-method");
  if (headerMethod === undefined) {
    return headerMismatch(id, "`Mcp-Method` header is required on every request.");
  }
  if (headerMethod !== method) {
    return headerMismatch(id, `\`Mcp-Method\` header (${headerMethod}) does not match the body method (${method}).`);
  }
  if (headerVersion === undefined) {
    return headerMismatch(id, "`MCP-Protocol-Version` header is required on every request.");
  }
  // Sürümün başlıkta bir karşılığı var: başlık gelmiş ama gövde alanı yoksa bu bir
  // başlık↔gövde uyuşmazlığıdır, `-32020`. Karşılığı olmayan `_meta` alanları aşağıda `-32602`.
  if (bodyVersion === undefined) {
    return headerMismatch(
      id,
      `\`MCP-Protocol-Version\` header is present but \`params._meta["${protocolVersionMetaKey}"]\` is missing.`
    );
  }
  if (headerVersion !== bodyVersion) {
    return headerMismatch(
      id,
      `\`MCP-Protocol-Version\` header (${headerVersion}) does not match \`params._meta["${protocolVersionMetaKey}"]\` (${bodyVersion}).`
    );
  }
  if (!SUPPORTED_PROTOCOL_VERSIONS.includes(headerVersion as typeof SUPPORTED_PROTOCOL_VERSIONS[number])) {
    return unsupportedProtocolVersion(id, headerVersion);
  }
  // Zorunlu ama başlıkta karşılığı olmayan alan: eksikse geçersiz parametre.
  if (capabilities === undefined) {
    return invalidParams(
      id,
      `\`params._meta["${clientCapabilitiesMetaKey}"]\` is required on every request.`
    );
  }
  if (typeof capabilities !== "object" || capabilities === null || Array.isArray(capabilities)) {
    return invalidParams(id, `\`params._meta["${clientCapabilitiesMetaKey}"]\` must be an object.`);
  }
  if (method === "tools/call") {
    const headerName = headerValue(request.headers, "mcp-name");
    if (headerName === undefined) {
      return headerMismatch(id, "`Mcp-Name` header is required for `tools/call`.");
    }
    const decoded = decodeHeaderSentinel(headerName);
    if (decoded !== parameters.name) {
      return headerMismatch(
        id,
        `\`Mcp-Name\` header (${decoded}) does not match \`params.name\` (${String(parameters.name)}).`
      );
    }
  }
  // Bildirime yanıt gövdesi yazılmaz; doğrulamayı geçmesi yeter.
  if (isNotification) return { status: 202, body: null };

  if (method === "tools/list") {
    return {
      status: 200,
      body: {
        jsonrpc: "2.0",
        id,
        result: { resultType: "complete", tools: mcpTools, _meta: resultMeta() }
      }
    };
  }
  if (method === "tools/call") {
    const name = parameters.name;
    if (typeof name !== "string" || !mcpTools.some((tool) => tool.name === name)) {
      return {
        status: 400,
        body: jsonRpcError(
          id,
          mcpErrorCodes.invalidParams,
          `Unknown tool \`${String(name)}\`. Call \`tools/list\` for the available tools.`
        )
      };
    }
    const result = await callTool(name, parameters.arguments, dependencies, request.agent ?? null);
    return {
      status: 200,
      body: {
        jsonrpc: "2.0",
        id,
        result: {
          resultType: "complete",
          content: [{ type: "text", text: result.text }],
          ...(result.structuredContent === undefined ? {} : { structuredContent: result.structuredContent }),
          isError: result.isError,
          _meta: resultMeta()
        }
      }
    };
  }
  return {
    status: 404,
    body: jsonRpcError(id, mcpErrorCodes.methodNotFound, `Unknown method \`${method}\`.`)
  };
};

/**
 * Ucu Fastify'a bağlar. Kimlik ve `Origin` denetimi `access-control.ts` içindeki tek yetki
 * kapısında yapılır — `/mcp` oraya açıkça yazılıdır, `/api/` muafiyetine düşmez.
 */
export const registerMcpEndpoint = (
  app: FastifyInstance,
  dependencies: McpEndpointDependencies
): void => {
  const notAllowed = async (_request: FastifyRequest, reply: FastifyReply): Promise<FastifyReply> => reply
    .code(405)
    .header("Allow", "POST")
    .send(mcpErrorBody(
      mcpErrorCodes.methodNotAllowed,
      "MCP ucu yalnız POST kabul eder; bu sürümde GET akışı yoktur."
    ));
  app.route({
    method: ["GET", "DELETE", "PUT", "PATCH", "OPTIONS"],
    url: mcpRoutePath,
    handler: notAllowed
  });
  app.post(mcpRoutePath, async (request, reply) => {
    const response = await handleMcpRequest({
      method: request.method,
      headers: request.headers,
      body: request.body,
      // Kapı token'ı çözdü; buraya yalnız kaydın kimliği ve adı geçer.
      agent: request.villaAgent === null
        ? null
        : { id: request.villaAgent.id, name: request.villaAgent.name }
    }, dependencies);
    if (response.body === null) return reply.code(response.status).send();
    return reply.code(response.status).send(response.body);
  });
};
