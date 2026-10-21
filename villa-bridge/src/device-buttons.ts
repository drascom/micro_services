import type { DeviceButtonActionView, DeviceButtonView } from "./types.js";

/**
 * Ham `action` değerlerindeki basış ekleri → kanonik basış kimliği.
 * Kanonik kimlik yalnızca sunum içindir; otomasyonlar ham `action` değerine bağlanır.
 */
const pressAliases = new Map<string, string>([
  ["single", "single"],
  ["click", "single"],
  ["press", "single"],
  ["tap", "single"],
  ["short", "single"],
  ["short_press", "single"],
  ["single_click", "single"],
  ["single_press", "single"],
  ["double", "double"],
  ["double_click", "double"],
  ["double_press", "double"],
  ["triple", "triple"],
  ["triple_click", "triple"],
  ["triple_press", "triple"],
  ["quadruple", "quadruple"],
  ["quadruple_click", "quadruple"],
  ["quadruple_press", "quadruple"],
  ["quintuple", "quintuple"],
  ["many", "many"],
  ["many_click", "many"],
  ["hold", "hold"],
  ["long", "hold"],
  ["long_press", "hold"],
  ["long_click", "hold"],
  ["release", "release"],
  ["hold_release", "release"],
  ["long_release", "release"],
  ["press_release", "release"],
  ["short_release", "release"]
]);

/** Basış eki taşımayan ama tek başına bir düğmeyi gösteren adlar (IKEA/Hue tarzı kumandalar). */
const namedButtonLabels = new Map<string, string>([
  ["on", "Açma düğmesi"],
  ["off", "Kapatma düğmesi"],
  ["toggle", "Değiştirme düğmesi"],
  ["up", "Yukarı düğmesi"],
  ["down", "Aşağı düğmesi"],
  ["left", "Sol düğme"],
  ["right", "Sağ düğme"],
  ["both", "Her iki düğme"],
  ["open", "Açma düğmesi"],
  ["close", "Kapatma düğmesi"],
  ["stop", "Durdurma düğmesi"],
  ["brightness_up", "Parlaklık artırma düğmesi"],
  ["brightness_down", "Parlaklık azaltma düğmesi"],
  ["arrow_left", "Sol ok düğmesi"],
  ["arrow_right", "Sağ ok düğmesi"],
  ["play_pause", "Oynat/duraklat düğmesi"],
  ["next", "İleri düğmesi"],
  ["previous", "Geri düğmesi"]
]);

const pressOrder = ["single", "double", "triple", "quadruple", "quintuple", "many", "hold", "release"];

const maximumPressTokens = 2;

interface ButtonIdentity {
  key: string | null;
  number: number | null;
}

interface ParsedAction extends ButtonIdentity {
  press: string;
}

const canonicalPress = (tokens: string[]): string | undefined => pressAliases.get(tokens.join("_"));

/** Basış eki ayrıldıktan sonra kalan parçalar düğmeyi adlandırır: `button_3` → 3, `arrow_left` → ad. */
const buttonIdentity = (tokens: string[]): ButtonIdentity => {
  const parts = tokens[0] === "button" && tokens.length > 1 ? tokens.slice(1) : tokens;
  if (parts.length === 0) return { key: null, number: null };
  const numeric = parts.length === 1 ? /^l?(\d{1,2})$/.exec(parts[0]) : null;
  if (numeric) return { key: null, number: Number(numeric[1]) };
  return { key: parts.join("_"), number: null };
};

/** Ham action değerini düğme + basış olarak çözer; kalıp tanınmazsa `null` döner. */
const parseAction = (raw: string): ParsedAction | null => {
  const tokens = raw.toLowerCase().split("_").filter((token) => token.length > 0);
  if (tokens.length === 0) return null;
  for (let size = maximumPressTokens; size >= 1; size -= 1) {
    if (tokens.length < size) continue;
    const suffix = canonicalPress(tokens.slice(tokens.length - size));
    if (suffix) return { ...buttonIdentity(tokens.slice(0, tokens.length - size)), press: suffix };
    // `single_left` gibi ters sıralı kalıplar: basış önde, düğme adı arkada.
    if (tokens.length > size) {
      const prefix = canonicalPress(tokens.slice(0, size));
      if (prefix) return { ...buttonIdentity(tokens.slice(size)), press: prefix };
    }
  }
  const label = namedButtonLabels.has(tokens.join("_")) ? tokens.join("_") : null;
  if (label) return { key: label, number: null, press: "single" };
  const bare = buttonIdentity(tokens);
  if (bare.number !== null) return { ...bare, press: "single" };
  return null;
};

const capitalize = (value: string): string =>
  value.length === 0 ? value : value[0].toLocaleUpperCase("tr-TR") + value.slice(1);

const defaultButtonName = (kind: DeviceButtonView["kind"], key: string | null, number: number | null): string => {
  if (kind === "numbered") return `${number}. düğme`;
  if (kind === "single") return "Düğme";
  if (kind === "ungrouped") return "Diğer eylemler";
  const slug = key ?? "";
  return namedButtonLabels.get(slug) ?? `${capitalize(slug.replaceAll("_", " "))} düğmesi`;
};

const kindRank: Record<DeviceButtonView["kind"], number> = {
  numbered: 0,
  named: 1,
  single: 2,
  ungrouped: 3
};

interface ButtonGroup {
  id: string;
  kind: DeviceButtonView["kind"];
  key: string | null;
  number: number | null;
  index: number;
  actions: DeviceButtonActionView[];
}

/**
 * `actionTypes` listesinden düğme alt varlıklarını türetir. Saf işlevdir: aynı girdi aynı çıktıyı verir.
 * Tanınmayan kalıplar kaybolmaz, `button:other` kovasında ham değerleriyle taşınır.
 */
export function deviceButtons(
  id: string,
  actionTypes: string[] | undefined,
  aliases: Map<string, string>
): DeviceButtonView[] {
  const groups = new Map<string, ButtonGroup>();
  const seen = new Set<string>();
  for (const value of actionTypes ?? []) {
    if (typeof value !== "string") continue;
    const raw = value.trim();
    if (raw.length === 0 || seen.has(raw)) continue;
    seen.add(raw);
    const parsed = parseAction(raw);
    const kind: DeviceButtonView["kind"] = parsed === null
      ? "ungrouped"
      : parsed.number !== null
        ? "numbered"
        : parsed.key === null
          ? "single"
          : "named";
    const slug = kind === "ungrouped"
      ? "other"
      : kind === "numbered"
        ? String(parsed?.number)
        : kind === "single"
          ? "main"
          : String(parsed?.key);
    const buttonId = `button:${slug}`;
    let group = groups.get(buttonId);
    if (!group) {
      group = {
        id: buttonId,
        kind,
        key: kind === "numbered" || kind === "single" ? null : parsed?.key ?? null,
        number: parsed?.number ?? null,
        index: groups.size,
        actions: []
      };
      groups.set(buttonId, group);
    }
    group.actions.push({ action: raw, press: parsed?.press ?? raw });
  }

  const pressRank = (press: string): number => {
    const index = pressOrder.indexOf(press);
    return index === -1 ? pressOrder.length : index;
  };

  return [...groups.values()]
    .sort((left, right) =>
      kindRank[left.kind] - kindRank[right.kind]
      || (left.number ?? 0) - (right.number ?? 0)
      || left.index - right.index
    )
    .map((group) => ({
      id: group.id,
      name: aliases.get(`${id}:${group.id}`) ?? defaultButtonName(group.kind, group.key, group.number),
      kind: group.kind,
      number: group.number,
      key: group.key,
      actions: group.kind === "ungrouped"
        ? group.actions
        : group.actions.slice().sort((left, right) =>
          pressRank(left.press) - pressRank(right.press) || left.action.localeCompare(right.action, "en")
        )
    }));
}
