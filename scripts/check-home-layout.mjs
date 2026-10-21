import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";

/* ANA EKRAN 1024x640 — YAPISAL DENETIM.
   Denetimin amaci bir tane: TABLETIN GERCEK CSS ALANINDA (1024x640) ana ekranin tasmamasi.
   Yerlesim degisti, amac degismedi — bu dosya yeni yerlesimi ayni titizlikle dogrular.

   YENI YERLESIM — DEMONUN SIRASI (not §6.1 + §7):
     1) Baslik UC BOLUM: sol = baglam + ekran basligi, orta = birlesik saat/hava, sag = evin
        sagligi. Saat/hava artik panonun bir KARTI degil, basligin ORTA hucresi. Evin durumu da
        artik uzun bir kart degil, sag hucredeki HAP (`#homeHealth`): kart ana ekrandan kalkti
        (emekli, ama silinmedi — bkz. 12r).
     2) Pano DORT satir, demonun `view-root` sirasi: "Hizli sahneler" basligi · dort sutunlu
        sahne seridi · "Odalar" basligi · oda izgarasi. Sahneler EN USTTE: eski alt bar
        (`position:fixed`) kalkti, cunku uzun kartlar sahneleri ekranin altina itiyordu.
     3) Oda izgarasi DIKEY: yatay sayfalama (`--rail-cards`/`--rail-unit`/`scroll-snap`) kalkti,
        satirlar alta iner. Kural: ILK ODA SATIRI kaydirmadan gorunur.
     4) Ana ekranin boyu SAYIYLA DEGIL AKISLA bulunur: `main` sutun akisi, `#home` esneyen
        eleman. Ustteki seritler (sistem uyarisi, mod seridi, PIN uyarisi) kendi GERCEK boylarini
        alir; panelde onlari taklit eden sabit (`--home-top`, `--admin-strip-h`) YOKTUR.
     5) Sol ray TAM BOY (`100dvh`), Profil `margin-top:auto` ile en altta.
     6) Pano duzenleme ikonlari ana ekranda BASILMAZ ama koddan silinmez (12e + 12f-2).

   ESKI DENETIMIN DEVRALINAN DERSI: metin karsilastirmasi tek basina yetmiyor. Burada iki katman
   var — (a) yapinin kendisi (hangi kural hangi medya blogunda, ne diyor), (b) SAYISAL BUTCE:
   bildirilmis `clamp()`/`calc()` degerleri 1024x640'ta gercekten cozulur ve mod seridinin UC
   halinde de (hic yok / tek satir / iki satir) toplam 640px'e sigar mi diye toplanir. Ikincisi
   olmasa "akis kuruldu ama icerik zaten sigmiyor" hatasi sessizce gecerdi. */

const VIEWPORT = { width: 1024, height: 640 };

/* Kart panosunun altinda kalmasi gereken en az bos alan: panonun kendi `min-height` bildirimi.
   Butce bunun altina duserse kartlar ezilir — sayiyi buraya gomuyoruz, CSS'ten okuyoruz. */
const BODY_FONT_PX = 16; // panelin taban yazi boyu
const TEXT_LINE_RATIO = 1.35; // sarmayan tek satirin kabaca kapladigi yukseklik carpani
const TOUCH_TARGET = 44; // not §7: temel dokunma hedefi

const squeeze = (value) => String(value).replace(/\s+/g, "");
const stripComments = (css) => css.replace(/\/\*[\s\S]*?\*\//g, "");

/* --- CSS'i kurallara ayirma -------------------------------------------------------------
   Minik bir ayristirici: her kuralin secicileri, govdesi ve icinde bulundugu medya kosullari.
   `indexOf` ile secici aramak yaniltiyordu (`.home-hub{` metni `#home .home-hub{` icinde de
   geciyor); burada secici TAM olarak eslesir. */
function parseRules(css) {
  const rules = [];
  const media = [];
  let prelude = "";
  let index = 0;
  while (index < css.length) {
    const char = css[index];
    if (char === "{") {
      const head = prelude.trim();
      prelude = "";
      if (head.startsWith("@")) {
        media.push(head);
        index += 1;
        continue;
      }
      let depth = 1;
      let end = index + 1;
      while (end < css.length && depth > 0) {
        if (css[end] === "{") depth += 1;
        else if (css[end] === "}") depth -= 1;
        end += 1;
      }
      rules.push({
        selectors: splitSelectors(head),
        media: media.slice(),
        body: css.slice(index + 1, end - 1),
        at: index
      });
      index = end;
      continue;
    }
    if (char === "}") {
      prelude = "";
      if (media.length) media.pop();
      index += 1;
      continue;
    }
    prelude += char;
    index += 1;
  }
  return rules;
}

/* Secici listesi UST DUZEY virgulden ayrilir: `:where(a,b)` icindeki virgul ayirac degildir.
   Duz `split(",")` burada `.home-hub`i `:where(...)` listesinin icinden cikarip ayri bir kural
   sanryordu — denetim yanlis kurali okuyordu. */
function splitSelectors(head) {
  const out = [];
  let depth = 0;
  let buffer = "";
  for (const char of head) {
    if (char === "(" || char === "[") depth += 1;
    else if (char === ")" || char === "]") depth -= 1;
    if (char === "," && depth === 0) {
      out.push(buffer.trim());
      buffer = "";
      continue;
    }
    buffer += char;
  }
  out.push(buffer.trim());
  return out.filter(Boolean);
}

/** `@media` kosulu verilen goruntu alaninda gecerli mi. Virgul = VEYA, `and` = VE. */
function mediaApplies(condition, viewport) {
  const text = condition.replace(/^@media/, "").trim();
  if (!text) return true;
  return text.split(",").some((arm) =>
    arm
      .split(/\band\b/)
      .map((one) => one.trim())
      .filter(Boolean)
      .every((feature) => featureApplies(feature, viewport))
  );
}

function featureApplies(feature, viewport) {
  const match = /^\(([a-z-]+)\s*:\s*([^)]+)\)$/.exec(squeeze(feature).replace(/\(([a-z-]+):/, "($1:"));
  if (!match) return false;
  const [, name, rawValue] = match;
  const value = rawValue.trim();
  const px = Number.parseFloat(value);
  switch (name) {
    case "orientation":
      return value === "landscape" ? viewport.width >= viewport.height : viewport.width < viewport.height;
    case "min-width":
      return viewport.width >= px;
    case "max-width":
      return viewport.width <= px;
    case "min-height":
      return viewport.height >= px;
    case "max-height":
      return viewport.height <= px;
    case "pointer":
      return value === "coarse"; // duvar tableti dokunmatik
    case "hover":
      return value === "none";
    case "prefers-reduced-motion":
      return false;
    case "prefers-color-scheme":
      return false;
    default:
      return false;
  }
}

/** Govdedeki ust duzey `ad:deger` ciftleri (parantez icindeki `;` yok, ama yine de guvenli). */
function declarations(body) {
  const out = [];
  let depth = 0;
  let buffer = "";
  for (const char of body) {
    if (char === "(") depth += 1;
    else if (char === ")") depth -= 1;
    if (char === ";" && depth === 0) {
      out.push(buffer);
      buffer = "";
      continue;
    }
    buffer += char;
  }
  out.push(buffer);
  return out
    .map((entry) => entry.trim())
    .filter(Boolean)
    .map((entry) => {
      const at = entry.indexOf(":");
      return { name: entry.slice(0, at).trim(), value: entry.slice(at + 1).trim() };
    })
    .filter((entry) => entry.name && entry.value);
}

function rulesFor(rules, selector, viewport) {
  return rules.filter(
    (rule) =>
      rule.selectors.includes(selector) &&
      (!viewport || rule.media.every((condition) => mediaApplies(condition, viewport)))
  );
}

/** Verilen goruntu alaninda, kaynak sirasina gore SON kazanan bildirim. */
function declaredValue(rules, selector, property, viewport) {
  let found = null;
  for (const rule of rulesFor(rules, selector, viewport)) {
    for (const entry of declarations(rule.body)) {
      if (entry.name === property) found = entry.value;
    }
  }
  return found;
}

function requireValue(rules, selector, property, viewport, label) {
  const value = declaredValue(rules, selector, property, viewport);
  assert(value !== null, `${label}: \`${selector}\` kuralinda \`${property}\` bildirimi yok.`);
  return value;
}

/* --- Uzunluk cozumleyici ----------------------------------------------------------------
   `clamp()`, `min()`, `max()`, `calc()`, `var()`, px/vh/vw/dvh — hepsi 1024x640'ta gercek sayiya
   iner. Bu olmadan "clamp yazdik, sigar herhalde" demis olurduk; butce sayilari CSS'ten gelmeli. */
function evaluateLength(expression, context) {
  const tokens = tokenize(String(expression));
  const parser = { tokens, index: 0, context };
  const value = parseSum(parser);
  assert(parser.index === tokens.length, `Uzunluk ifadesi cozulemedi: ${expression}`);
  return value;
}

function tokenize(text) {
  const tokens = [];
  const pattern = /\s*(var\(|clamp\(|min\(|max\(|calc\(|\(|\)|,|\+|\*|\/|--[a-zA-Z0-9-]+|-?[\d.]+(?:px|vh|vw|dvh|dvw|%)?|-(?![\d.]))/gy;
  let index = 0;
  while (index < text.length) {
    pattern.lastIndex = index;
    const match = pattern.exec(text);
    if (!match) break;
    tokens.push(match[1]);
    index = pattern.lastIndex;
  }
  return tokens;
}

function parseSum(parser) {
  let value = parseProduct(parser);
  while (parser.tokens[parser.index] === "+" || parser.tokens[parser.index] === "-") {
    const operator = parser.tokens[parser.index];
    parser.index += 1;
    const right = parseProduct(parser);
    value = operator === "+" ? value + right : value - right;
  }
  return value;
}

function parseProduct(parser) {
  let value = parseTerm(parser);
  while (parser.tokens[parser.index] === "*" || parser.tokens[parser.index] === "/") {
    const operator = parser.tokens[parser.index];
    parser.index += 1;
    const right = parseTerm(parser);
    value = operator === "*" ? value * right : value / right;
  }
  return value;
}

function parseTerm(parser) {
  const token = parser.tokens[parser.index];
  assert(token !== undefined, "Uzunluk ifadesi beklenmedik yerde bitti.");
  if (token === "clamp(" || token === "min(" || token === "max(") {
    parser.index += 1;
    const args = parseArguments(parser);
    if (token === "clamp(") {
      assert(args.length === 3, "clamp() uc deger ister.");
      return Math.min(Math.max(args[1], args[0]), args[2]);
    }
    return token === "min(" ? Math.min(...args) : Math.max(...args);
  }
  if (token === "calc(" || token === "(") {
    parser.index += 1;
    const value = parseSum(parser);
    assert(parser.tokens[parser.index] === ")", "Kapanmayan parantez.");
    parser.index += 1;
    return value;
  }
  if (token === "var(") {
    parser.index += 1;
    const name = parser.tokens[parser.index];
    parser.index += 1;
    let fallback = null;
    if (parser.tokens[parser.index] === ",") {
      parser.index += 1;
      fallback = parseSum(parser);
    }
    assert(parser.tokens[parser.index] === ")", `var(${name}) kapanmiyor.`);
    parser.index += 1;
    const declared = parser.context.vars[name];
    if (declared === undefined) {
      assert(fallback !== null, `Tanimsiz degisken: ${name}`);
      return fallback;
    }
    return evaluateLength(declared, parser.context);
  }
  parser.index += 1;
  return toPixels(token, parser.context.viewport);
}

function parseArguments(parser) {
  const args = [];
  for (;;) {
    args.push(parseSum(parser));
    if (parser.tokens[parser.index] === ",") {
      parser.index += 1;
      continue;
    }
    assert(parser.tokens[parser.index] === ")", "Fonksiyon kapanmiyor.");
    parser.index += 1;
    return args;
  }
}

function toPixels(token, viewport) {
  const number = Number.parseFloat(token);
  assert(Number.isFinite(number), `Sayiya cevrilemedi: ${token}`);
  if (token.endsWith("vh") || token.endsWith("dvh")) return (number * viewport.height) / 100;
  if (token.endsWith("vw") || token.endsWith("dvw")) return (number * viewport.width) / 100;
  return number;
}

/** `padding:14px 20px 20px` gibi kisayollarin UST ve ALT degeri. */
function boxSides(shorthand) {
  const parts = splitTopLevel(shorthand);
  const top = parts[0];
  const bottom = parts.length >= 3 ? parts[2] : parts[0];
  return { top, bottom };
}

function splitTopLevel(value) {
  const parts = [];
  let depth = 0;
  let buffer = "";
  for (const char of value) {
    if (char === "(") depth += 1;
    else if (char === ")") depth -= 1;
    if (/\s/.test(char) && depth === 0) {
      if (buffer) parts.push(buffer);
      buffer = "";
      continue;
    }
    buffer += char;
  }
  if (buffer) parts.push(buffer);
  return parts;
}

export async function assertHomeLayout(projectRoot) {
  const rawCss = await readFile(path.join(projectRoot, "public", "css", "panel.css"), "utf8");
  const css = stripComments(rawCss);
  const flat = squeeze(css);
  const html = await readFile(path.join(projectRoot, "public", "index.html"), "utf8");
  const widgets = await readFile(path.join(projectRoot, "public", "js", "50-widgets.js"), "utf8");
  const homeJs = await readFile(path.join(projectRoot, "public", "js", "40-home.js"), "utf8");
  const coreJs = await readFile(path.join(projectRoot, "public", "js", "10-core.js"), "utf8");
  const rules = parseRules(css);

  /* 1) SAAT/HAVA BASLIGIN ICINDE, PANONUN ICINDE DEGIL.
     Bu tek cumle yerlesimin tamami: blok basliktan cikip panoya donerse ana ekran yeniden bir
     kart boyu kaybeder ve 1024x640'ta alt serit sikisir. */
  const homeSection = between(html, '<section id="home" class=', '<section id="devices"');
  const head = section(homeSection, '<header class="page-head">', "</header>");
  const board = homeSection.slice(homeSection.indexOf('<div id="widgetBoard"'));
  assert(head.includes('id="homeHub"'), "Birlesik saat/hava blogu (#homeHub) ana ekran basliginda degil.");
  assert(!board.includes('id="homeHub"'), "Saat/hava blogu hala panonun (widgetBoard) icinde.");
  assert(head.includes('class="home-heading"'), "Basligin sol bolumu (baglam + ekran basligi) yok.");
  assert(head.includes('class="home-title"'), "Ekran basligi (.home-title) yok: sol bolum yalniz baglam yazisi olamaz.");
  assert(head.includes('class="home-status"'), "Basligin sag bolumu (evin sagligi) yok.");
  assert(head.indexOf('class="home-heading"') < head.indexOf('id="homeHub"'), "Sol bolum saat/hava blogundan sonra geliyor.");
  assert(head.indexOf('id="homeHub"') < head.indexOf('class="home-status"'), "Sag bolum saat/hava blogundan once geliyor.");

  /* 2) IKI AYRI TIKLANABILIR BOLGE VE ARADAKI CIZGI. Pencereler AYNEN korunur: yalniz tetikleyici
     tasindi, `#clockDialog`/`#weatherDialog` yerinde. */
  const hub = section(head, '<section id="homeHub"', "</section>");
  for (const [id, label] of [["hubClockZone", "saat"], ["hubWeatherZone", "hava"]]) {
    const at = hub.indexOf(`id="${id}"`);
    assert(at >= 0, `Hub'da ${label} bolgesi yok.`);
    const tag = hub.slice(hub.lastIndexOf("<", at), hub.indexOf(">", at));
    assert(tag.startsWith("<button"), `${label} bolgesi dugme degil: tiklanabilir olmali.`);
    assert(tag.includes('aria-haspopup="dialog"'), `${label} bolgesi pencere actigini soylemiyor.`);
  }
  const dividerAt = hub.indexOf('class="hub-divider"');
  assert(dividerAt > hub.indexOf('id="hubClockZone"'), "Iki bolge arasindaki cizgi yok ya da yanlis yerde.");
  assert(dividerAt < hub.indexOf('id="hubWeatherZone"'), "Ayrac cizgisi iki bolgenin ARASINDA degil.");
  assert(!html.includes("hub-locked"), "Kaldirilan `.hub-locked` rozeti hala isaretlemede.");
  for (const dialog of ["clockDialog", "weatherDialog"]) {
    assert(html.includes(`id="${dialog}"`), `Korunmasi gereken pencere kaybolmus: #${dialog}.`);
  }

  /* 3) HUB SUTUNU VE SONME MANTIGI TAMAMEN KALKTI. Ikisi de eski iki sutunlu panonun parcasiydi;
     biri geri gelirse ray yeniden dar kalir ve kartlar saat/hava blogunun ustune biner. */
  assert(!flat.includes("--hub-column"), "Kaldirilan hub sutunu (--hub-column) hala tanimli.");
  assert(!flat.includes("hub-hidden"), "Kaldirilan hub sonme mantigi (.hub-hidden) hala CSS'te.");
  assert(!widgets.includes("hub-hidden"), "50-widgets.js hala hub'i sonduruyor (hub-hidden).");
  assert(!widgets.includes("updateHubVisibility"), "50-widgets.js'te `updateHubVisibility` hala duruyor.");

  /* 4) BASLIK IZGARASI UC BOLUM VE ORTA HUCRE GERCEKTEN ORTADA.
     Yan hucreler BIREBIR ayni belirtec olmali: biri `auto` olsaydi orta hucre icerik genisligine
     gore kayardi ve "ortada" iddiasi yalnizca goze bagli kalirdi. */
  const railViewport = VIEWPORT;
  const headColumns = requireValue(rules, "#home .page-head", "grid-template-columns", railViewport, "Ana ekran basligi");
  const tracks = splitTopLevel(headColumns);
  assert.equal(tracks.length, 3, `Ana ekran basligi uc bolum degil: ${headColumns}`);
  assert.equal(squeeze(tracks[0]), squeeze(tracks[2]), "Basligin iki yan hucresi esit degil: orta hucre ortalanmaz.");
  assert.equal(squeeze(tracks[0]), "minmax(0,1fr)", "Yan hucreler daralabilir olmali (minmax(0,1fr)).");
  const middle = evaluateLength(tracks[1], { viewport: railViewport, vars: {} });
  assert(
    /clamp\(/.test(tracks[1]) && /vw/.test(tracks[1]),
    "Orta hucre sabit px: `clamp()` + goruntu birimi olmali."
  );
  /* Tarih ile hava aciklamasi iki esit bilgi grubu olarak eksiksiz okunabilsin. 1024px tablette
     eski 440px ust siniri iki tarafta da uc nokta uretiyordu; orta hucre bilincli olarak genis. */
  assert(middle >= 480 && middle <= 560, `Orta hucre 1024px'te ${Math.round(middle)}px: saat/hava alani icin uygun degil.`);
  assert.equal(declaredValue(rules, "#home .home-heading", "grid-column", railViewport), "1", "Sol bolum birinci hucrede degil.");
  assert.equal(declaredValue(rules, "#home .home-hub", "grid-column", railViewport), "2", "Saat/hava blogu orta hucrede degil.");
  assert.equal(declaredValue(rules, "#home .home-status", "grid-column", railViewport), "3", "Ev sagligi ucuncu hucrede degil.");

  /* 5) HUB KART DEGIL: seffaf zemin, ince bordur, hafif ic isik — ve TEK SATIR. */
  const hubRule = rulesFor(rules, ".home-hub", null)[0];
  assert(hubRule, "`.home-hub` kurali bulunamadi.");
  const hubBody = squeeze(hubRule.body);
  assert(hubBody.includes("background:transparent"), "Saat/hava blogunun zemini seffaf degil: ayri bir kart olur.");
  assert(/border:1pxsolid/.test(hubBody), "Saat/hava blogunda ince bordur yok.");
  assert(hubBody.includes("box-shadow:var(--glass-sheen)"), "Saat/hava blogunda hafif ic isik (--glass-sheen) yok.");
  assert(!/box-shadow:[^;]*var\(--card-shadow\)/.test(hubBody), "Saat/hava blogu kart golgesi tasiyor: kart gorunumu.");
  assert(hubBody.includes("display:flex"), "Saat/hava blogu tek satirlik serit degil.");
  assert(!/flex-direction:column/.test(hubBody), "Saat/hava blogu alt alta diziliyor: basligin ikinci satirini acar.");
  const dividerBody = squeeze(rulesFor(rules, ".hub-divider", null)[0]?.body || "");
  assert(dividerBody.includes("width:1px"), "Ayrac dikey degil: iki bolgeyi degil, alt/ust ayirir.");

  /* 6) DOKUNMA HEDEFLERI (not §7: 44px taban, ana ekran dugmeleri 56px). */
  const zoneMin = evaluateLength(
    requireValue(rules, ".hub-zone", "min-height", railViewport, "Hub bolgesi"),
    { viewport: railViewport, vars: {} }
  );
  assert(zoneMin >= TOUCH_TARGET, `Hub bolgesi ${zoneMin}px: dokunma hedefi 44px'in altinda.`);
  const vars = collectVariables(rules, railViewport);
  const actionHeight = evaluateLength(vars["--head-action-h"], { viewport: railViewport, vars });
  assert(actionHeight >= 56, `Ana ekran basligindaki dugmeler ${Math.round(actionHeight)}px: 56px hedefinin altinda.`);

  /* 7) ANA EKRANIN BOYU AKISTAN GELIYOR — sabit sistem/PIN çubuklarının payı gövde sınıflarıyla
     verilir; görünüm yüksekliğine gömülü ikinci bir sabit hesap yoktur. */
  assert(!flat.includes("--home-top"), "Elde sayilan serit payi (--home-top) geri gelmis.");
  assert(!flat.includes("--admin-strip-h"), "Elde sayilan mod seridi boyu (--admin-strip-h) geri gelmis.");
  const homeHeight = declaredValue(rules, "#home.active", "height", railViewport);
  assert(
    !homeHeight || !/dvh|vh/.test(homeHeight),
    `Ana ekranin boyu hala goruntu alanindan cikarilan bir hesap: ${homeHeight}`
  );
  assert.equal(
    squeeze(requireValue(rules, "#home.active", "flex", railViewport, "Ana ekran")),
    "1 1 auto".replace(/\s/g, ""),
    "Ana ekran akisin esneyen elemani degil: ustteki seritler payini kendiliginden alamaz."
  );
  assert.equal(
    declaredValue(rules, "#home.active", "min-height", railViewport),
    "0",
    "Ana ekran `min-height:0` degil: esneme calismaz, tasma alta kacar."
  );
  const mainSelector = 'body[data-active-view="home"] main';
  assert.equal(
    declaredValue(rules, mainSelector, "display", railViewport),
    "flex",
    "Ana ekranin kabi (`main`) sutun akisi degil."
  );
  assert.equal(declaredValue(rules, mainSelector, "flex-direction", railViewport), "column", "`main` sutun yonunde akmiyor.");
  assert(
    /dvh/.test(declaredValue(rules, mainSelector, "min-height", railViewport) || ""),
    "`main` en az bir ekran boyu degil: kisa icerikte ana ekran ekrani doldurmaz."
  );

  /* 8) SAYISAL BUTCE — 1024x640, sabit üst çubukların dört hali. PIN çubuğu artık akışta büyük
     bir kart değildir; `main` üst dolgusu tek sistem çubuğu ve iki çubuğun yığıldığı durum için
     CSS'te açıkça tanımlıdır. */
  const context = { viewport: railViewport, vars };
  const px = (value) => evaluateLength(value, context);
  const mainPadding = boxSides(requireValue(rules, "main", "padding", railViewport, "Sayfa kabi"));
  const mainPadTop = px(mainPadding.top);
  const mainPadBottom = px(
    declaredValue(rules, mainSelector, "padding-bottom", railViewport) || mainPadding.bottom
  );

  assert.equal(
    declaredValue(rules, ".admin-mode-strip", "display", railViewport),
    "contents",
    "Eski buyuk yonetici karti geri gelmis."
  );
  assert.equal(
    declaredValue(rules, ".admin-mode-strip>.default-pin-banner", "position", railViewport),
    "fixed",
    "Varsayilan PIN uyarisi sabit sistem cubugu degil."
  );
  const systemBarPadding = px(requireValue(rules, "body.has-system-alert main", "padding-top", railViewport, "Sistem cubugu"));
  const pinBarPadding = px(requireValue(rules, "body.has-default-pin-warning main", "padding-top", railViewport, "PIN cubugu"));
  const stackedBarPadding = px(requireValue(rules, "body.has-system-alert.has-default-pin-warning main", "padding-top", railViewport, "Yigili ust cubuklar"));

  const headingHeight =
    px(requireValue(rules, ".eyebrow", "font-size", railViewport, "Baglam yazisi")) * TEXT_LINE_RATIO +
    px(splitTopLevel(requireValue(rules, ".home-title", "font", railViewport, "Ekran basligi").split("/")[0]).pop()) *
      TEXT_LINE_RATIO;
  /* Sag hucre iki kademe: (gosterge cipleri VE saglik hapi ayni satirda) + eylem dugmeleri.
     Hap satiri kendi basina buyutmesin diye satirin boyu ikisinin BUYUGU. */
  const statusHeight =
    Math.max(
      px(requireValue(rules, ".summary-chip", "min-height", railViewport, "Ev sagligi gostergesi")),
      px(requireValue(rules, ".home-health", "min-height", railViewport, "Ev sagligi hapi"))
    ) +
    px(requireValue(rules, ".home-status", "gap", railViewport, "Ev sagligi hucresi")) +
    actionHeight;
  const hubPadding = boxSides(requireValue(rules, ".home-hub", "padding", railViewport, "Saat/hava blogu"));
  const hubLine = px(vars["--hub-line"]);
  const hubTime = px(vars["--hub-time-size"]);
  const zonePadding = boxSides(requireValue(rules, ".hub-zone", "padding", railViewport, "Hub bolgesi"));
  const hubContent = Math.max(
    zoneMin,
    hubTime + 2 * hubLine * TEXT_LINE_RATIO + px(zonePadding.top) + px(zonePadding.bottom),
    px(requireValue(rules, ".hub-zone-weather", "--weather-scene-size", railViewport, "Hava ikonu"))
  );
  const hubHeight = px(hubPadding.top) + px(hubPadding.bottom) + hubContent;
  const headHeight = Math.max(headingHeight, statusHeight, hubHeight);
  const headGap = px(vars["--home-head-gap"]);

  const boardRowGap = px(requireValue(rules, "#home .widget-board", "row-gap", railViewport, "Kart panosu"));
  const quickHeight = px(
    requireValue(rules, '#home [data-widget="quick"]', "min-height", railViewport, "Hizli erisim seridi")
  );
  /* Dış pano UC satir: sahne basligi · sahne seridi · icerik rayi. Favoriler ile onun altindaki
     Odalar basligi ve oda izgarasi bu rayin icinde normal belge akisinda devam eder. */
  const sectionHead = px(requireValue(rules, ".home-section-head", "min-height", railViewport, "Bolum basligi"));
  /* "Ilk oda satiri kaydirmadan gorunmeli" kurali BURADA olculur: panonun `min-height` bildirimi
     bir taban, gercek kisit ise oda kartinin KENDI boyu. Kart boyu buyurse butce de buyur. */
  const roomRow = px(requireValue(rules, "#home", "--room-card-h", railViewport, "Oda karti boyu"));
  const boardMin = roomRow;

  const fixed = headHeight + headGap + 3 * boardRowGap + 2 * sectionHead + quickHeight + mainPadBottom;
  const states = [
    { label: "ust cubuk yok", top: mainPadTop },
    { label: "sistem cubugu", top: systemBarPadding },
    { label: "PIN cubugu", top: pinBarPadding },
    { label: "sistem ve PIN cubuklari", top: stackedBarPadding }
  ];
  for (const state of states) {
    const total = fixed + state.top + boardMin;
    assert(
      total <= VIEWPORT.height,
      `1024x640 tasiyor — ${state.label}: gereken ${Math.round(total)}px, ekran ${VIEWPORT.height}px. ` +
        `(baslik ${Math.round(headHeight)}px, ust pay ${Math.round(state.top)}px, sahne seridi ${Math.round(quickHeight)}px, ` +
        `bolum basliklari 2x${Math.round(sectionHead)}px, ilk oda satiri ${Math.round(roomRow)}px)`
    );
  }

  /* 9) ANA EKRANIN SIRASI: baslik · "Hizli sahneler" · Favoriler · "Odalar".
     Iki sart birden aranir, cunku biri digerini kurtarmaz:
       (a) SERIT AKISTA. Ekrana `fixed`lenmis bir alt bar olsaydi butce yalan olurdu — serit
           icerigin ustune biner, tasma goze gorunur ama hesaba hic girmez.
       (b) SAHNELER ODA KARTLARINDAN ONCE. Bu, kullanicinin tablette gordugu asil sikayetti:
           uzun kartlar dort sahneyi ekranin altina itiyordu. Sira hem DOM'da hem CSS'in sabit
           satir numaralarinda denetlenir; ikisi ayrisirsa ekran DOM'a degil CSS'e uyar. */
  const quickPosition = declaredValue(rules, '#home [data-widget="quick"]', "position", railViewport);
  assert.equal(quickPosition, "relative", "Hizli erisim seridi 1024x640'ta akista degil.");
  const homeRows = [
    ['#home .home-section-head[data-home-section="scenes"]', "1", "sahne bolumunun basligi"],
    ['#home [data-widget="quick"]', "2", "hizli sahne seridi"],
    ["#home .widget-rail", "3", "favoriler ve oda izgarasi"]
  ];
  for (const [selector, row, label] of homeRows) {
    assert.equal(
      declaredValue(rules, selector, "grid-row", railViewport),
      row,
      `Ana ekranin ${label} panonun ${row}. satirinda degil: sira demoyla ayni olmali.`
    );
  }
  assert(html.includes('data-widget="quick"'), "Hizli erisim widget'i panel isaretlemesinde bulunamadi.");
  /* DOM sirasi da ayni: sahne serdi oda izgarasindan ONCE yazilmali (klavye ve ekran okuyucu
     grid satir numarasini degil, DOM'u izler). */
  const scenesHeadAt = board.indexOf('data-home-section="scenes"');
  const quickAt = board.indexOf('data-widget="quick"');
  const railAt = board.indexOf('id="widgetRail"');
  const favoritesAt = board.indexOf('data-widget="favorites"');
  const roomsHeadAt = board.indexOf('data-home-section="rooms"');
  const roomGridAt = board.indexOf('id="roomWidgetGrid"');
  assert(scenesHeadAt >= 0, 'Sahne bolumunun basligi yok: `data-home-section="scenes"`.');
  assert(roomsHeadAt >= 0, 'Oda bolumunun basligi yok: `data-home-section="rooms"`.');
  assert(scenesHeadAt < quickAt, "Sahne basligi seritten sonra geliyor.");
  assert(quickAt < railAt, "Hizli sahne seridi ana icerik rayinin ICINDE ya da altinda.");
  assert(railAt < favoritesAt, "Favoriler karti ana icerik rayinin disinda.");
  assert(favoritesAt < roomsHeadAt, "Odalar basligi Favoriler kartinin ustunde: istenen sira bozuk.");
  assert(roomsHeadAt < roomGridAt, "Odalar basligi oda izgarasindan sonra geliyor.");
  /* Bolum baslıklari birer AD SATIRIDIR: icinde dugme olsaydi ya ikinci bir gezinme mekanizmasi
     acilirdi ya da 44px dokunma hedefi butceyi tasirirdi. */
  for (const marker of ["scenes", "rooms"]) {
    const at = board.indexOf(`data-home-section="${marker}"`);
    const head = board.slice(at, board.indexOf("</div>", at));
    assert(!head.includes("<button"), `Bolum basligi dugme tasiyor (${marker}): ikinci bir gezinme yolu acilmis.`);
  }

  /* 10) RAY TAM GENISLIK VE KART BIRIMI RAYIN GENISLIGINI DUSUYOR.
     Eski birim hub sutununu dusuyordu; sol sabit ray geldiginde o pay yanlis ada bagli kaldi. */
  const railRule = rulesFor(rules, "#home .widget-rail", railViewport);
  for (const rule of railRule) {
    for (const entry of declarations(rule.body)) {
      assert(
        !/^padding-left$|^scroll-padding-left$/.test(entry.name),
        "Ray hala sol sutun payi tasiyor: hub sutunu kalkti, pay da kalkmali."
      );
    }
  }
  /* 10a) ODA IZGARASI: YATAY SAYFALAMA DEGIL, DIKEY IZGARA.
     Eski ray tek satirda sayfa sayfa kayiyordu; "ilk oda satiri kaydirmadan gorunmeli" kurali
     ancak satirlarin ALTA inmesiyle anlam kazanir. Sutun sayisi sabit olamaz: 1024'te uc,
     1280'de dort cikmali, 800'de kart 44px hedeflerinin altina ezilmemeli. */
  const railColumns = squeeze(
    requireValue(rules, "#home .room-widget-grid", "grid-template-columns", railViewport, "Oda izgarasi")
  );
  assert(
    railColumns.includes("minmax(") && /clamp\(|vw|%/.test(railColumns),
    `Ana ekranin oda izgarasi sabit sutun: ${railColumns}`
  );
  assert.equal(
    squeeze(declaredValue(rules, "#home .room-widget-grid", "grid-auto-rows", railViewport) || ""),
    "var(--room-card-h)",
    "Oda satiri boyunu tek kaynaktan (--room-card-h) almiyor: butce ile ekran ayrisir."
  );
  assert.equal(
    declaredValue(rules, "#home .room-widget-grid", "overflow", railViewport),
    "visible",
    "Oda izgarasi kendi icinde kayiyor: ana sayfanin tek kaydirma akisi bozulmus."
  );
  for (const [name, label] of [
    ["--rail-unit", "kart birimi"],
    ["--rail-cards", "sayfadaki kart sayisi"],
    ["--rail-gap", "ray payi"]
  ]) {
    assert(!flat.includes(name), `Kaldirilan yatay sayfalama degiskeni geri gelmis: ${name} (${label}).`);
  }
  assert(!flat.includes("scroll-snap-type:xmandatory"), "Yatay sayfalama (scroll-snap) geri gelmis.");
  assert(
    !flat.includes(".group-panel") && !flat.includes("#groupPanel"),
    "Kaldirilan grup paneli (.group-panel) hala CSS'te: ray tam genisligin tek katmani olmali."
  );
  assert(!html.includes('id="groupPanel"'), "Kaldirilan grup paneli hala isaretlemede.");

  /* 10b) HIZLI SAHNE SERIDI — DORT SUTUN, ~62px KART, ACIKLAMA YOK (not §6.2 + §12).
     Serit oda sekmesi olmaktan cikti; eski sekme kabugunun (kaydirma oklari, "yeni grup"
     dugmesi, tablist) geri gelmesi hem 1024x640 butcesini hem de "tek dokunusta rutin" akisini
     bozar, o yuzden yoklugu de denetlenir. */
  const strip = section(homeSection, '<section class="dashboard-widget widget-wide quick-control-widget"', "</section>");
  assert(strip.includes('id="quickScenes"'), "Hizli sahne seridi (#quickScenes) panel isaretlemesinde yok.");
  for (const [needle, label] of [
    ['id="homeTabs"', "eski sekme kaydiricisi"],
    ['id="homeTabList"', "eski sekme listesi"],
    ['id="createHomeGroup"', '"yeni grup" dugmesi'],
    ['id="quickScrollLeft"', "sol kaydirma oku"],
    ['id="quickScrollRight"', "sag kaydirma oku"],
    ['role="tablist"', "sekme rolu"],
    ["<small", "aciklama satiri"],
    ["<p", "aciklama paragrafi"]
  ]) {
    assert(!strip.includes(needle), `Hizli sahne seridinde olmamasi gereken oge: ${label}.`);
  }
  assert(!flat.includes("villa-home-tab"), "Kaldirilan sekme kaydi (villa-home-tab) geri gelmis.");
  const sceneColumns = squeeze(
    requireValue(rules, ".quick-scene-grid", "grid-template-columns", railViewport, "Hizli sahne seridi")
  );
  assert.equal(sceneColumns, "repeat(4,minmax(0,1fr))", `Serit dort sutun degil: ${sceneColumns}`);
  const sceneHeightRaw = vars["--scene-h"];
  assert(sceneHeightRaw, "Sahne kartinin boyu (--scene-h) tanimli degil.");
  assert(
    /clamp\(/.test(sceneHeightRaw) && /vh/.test(sceneHeightRaw),
    "Sahne kartinin boyu sabit px: `clamp()` + goruntu birimi olmali."
  );
  const sceneHeight = px(sceneHeightRaw);
  assert(sceneHeight >= TOUCH_TARGET, `Sahne karti ${Math.round(sceneHeight)}px: dokunma hedefi 44px'in altinda.`);
  assert(
    sceneHeight >= 58 && sceneHeight <= 66,
    `Sahne karti 1024x640'ta ${Math.round(sceneHeight)}px: not §6.2 ~62px istiyor.`
  );
  /* Seridin bildirilen boyu kartin GERCEK boyunu tasimali: butce `min-height`i okuyor, kart
     ondan tasarsa hesap dogru ama ekran yanlis olurdu. */
  assert(
    quickHeight >= sceneHeight,
    `Serit ${Math.round(quickHeight)}px, kart ${Math.round(sceneHeight)}px: kart seride sigmiyor.`
  );
  for (const selector of ["#home .quick-control-widget .strip-row", "#home .quick-scene"]) {
    assert.equal(
      squeeze(declaredValue(rules, selector, "height", railViewport) || ""),
      "var(--scene-h)",
      `\`${selector}\` boyunu tek kaynaktan (--scene-h) almiyor: iki yerde yazilan sayi kayar.`
    );
  }

  /* 11) JS KOSULU = CSS KOSULU. Hub sonme mantigi kalkti ama kaydirma oklari hala bu kosula
     bagli; ayrisirsa oklar yanlis kirilimda calisir. */
  const twoColumnMedia = "(orientation:landscape) and (max-height:900px),(orientation:landscape) and (min-width:1000px)";
  const boardRule = rulesFor(rules, "#home .widget-board", null).find((rule) =>
    rule.media.some((condition) => squeeze(condition) === `@media${squeeze(twoColumnMedia)}`)
  );
  assert(boardRule, "Panonun yatay kip kurali beklenen kirilimda degil; JS tarafi da guncellenmeli.");
  const guard = widgets.match(/const\s+homeOverlayMediaQuery\s*=\s*"([^"]+)"/);
  assert(guard, "50-widgets.js icinde homeOverlayMediaQuery tanimi yok.");
  assert.equal(
    squeeze(guard[1]),
    squeeze(twoColumnMedia),
    "JS'teki yerlesim kosulu CSS blogunun kosulundan farkli: kaydirma oklari yanlis kirilimda calisir."
  );
  assert(widgets.includes("matchMedia(homeOverlayMediaQuery)"), "Kaydirma mantigi ortak yerlesim kosulunu kullanmiyor.");

  /* 12) ODA KARTLARI URETILIYOR — elle kurulan pano degil.
     Ana ekranin ve Odalar gorunumunun kartlari `35-generator.js`ten dogar. Panelin ikinci bir
     "odada ne var" kurali yazmasi bu asamanin tam tersi olurdu, o yuzden dogrudan denetlenir. */
  for (const [needle, label] of [
    ["genCardModelForGroup", "oda karti modeli"],
    ["genUngroupedCardModel", "grupsuz cihaz karti modeli"]
  ]) {
    assert(homeJs.includes(needle), `40-home.js jeneratoru kullanmiyor: ${label} (${needle}).`);
  }
  assert(
    /const cardModelForGroup\s*=/.test(homeJs),
    "Oda karti modeli tek bir kaynaktan (`cardModelForGroup`) alinmiyor: paralel model riski."
  );

  /* 12b) CIHAZ GORUNURLUGU KART SEVIYESINDE. Anahtar KAPSAM almazsa bir kartta gizlenen cihaz
     yine her kartta gizlenir — bu asamanin duzelttigi hata tam olarak buydu. Kapsamsiz ESKI
     anahtar da okunabilir kalmali, yoksa kullanicinin bugunku tercihleri sessizce silinir. */
  const visibilityKeyFn = coreJs.match(/const\s+tileVisibilityKey\s*=\s*\(([^)]*)\)/);
  assert(visibilityKeyFn, "10-core.js icinde `tileVisibilityKey` tanimi yok.");
  const visibilityKeyArgs = visibilityKeyFn[1].split(",").map((one) => one.trim());
  assert.equal(
    visibilityKeyArgs[0],
    "scope",
    `Gorunurluk anahtari kapsam almiyor (ilk parametre \`${visibilityKeyArgs[0]}\`): kartlar birbirinden ayrilamaz.`
  );
  assert(
    /const\s+legacyTileVisibilityKey\s*=/.test(coreJs),
    "Kapsamsiz eski gorunurluk anahtari (`legacyTileVisibilityKey`) kalkmis: eski tercihler kaybolur."
  );
  assert(
    /const\s+parseVisibilityKey\s*=/.test(coreJs),
    "Gorunurluk anahtari cozumleyicisi (`parseVisibilityKey`) yok: sunucuya kapsam yazilamaz."
  );
  assert(
    homeJs.includes("tileVisibilityScopes"),
    "Kapsamsiz kaydin gocu (`tileVisibilityScopes`) yok: eski tercih ilk dokunusta kaybolur."
  );

  /* 12c) SOL RAYDA ODALAR SATIRI, TEK GEZINME MEKANIZMASI ILE.
     Satir `.nav-button` + `data-view` tasimali; ayri bir tiklama/isaret mekanizmasi acilirsa
     "aktif satir" iki yerden yazilir ve kaciniz. Ev modunun uc satiri da yonetici kisitsizdir. */
  const rail = section(html, '<aside class="side-rail">', "</aside>");
  for (const view of ["home", "rooms", "routines"]) {
    const at = rail.indexOf(`data-view="${view}"`);
    assert(at >= 0, `Sol rayda ev modu satiri yok: ${view}.`);
    const tag = rail.slice(rail.lastIndexOf("<button", at), rail.indexOf(">", at));
    assert(
      tag.includes("nav-button") && tag.includes("rail-button"),
      `Ray satiri ortak gezinme sinifini tasimiyor (${view}): ikinci bir mekanizma acilmis.`
    );
    assert(
      !tag.includes("data-admin-only"),
      `Ev modunun satiri yonetici kisitli olmus: ${view}.`
    );
  }
  assert(
    rail.indexOf('data-view="home"') < rail.indexOf('data-view="rooms"'),
    "Rayda Odalar satiri Ev'den once geliyor."
  );
  assert(
    rail.indexOf('class="rail-bottom"') > rail.indexOf('data-view="rooms"'),
    "Profil rayin en altinda degil."
  );

  /* 12d) ODALAR GORUNUMU ANA EKRANIN DISINDA. Icerideyse 1024x640 butcesi yalan soyler:
     kartlar ana ekranin payini yer ama hesaba girmez. */
  assert(html.includes('<section id="rooms" class="view">'), "Odalar gorunumu (#rooms) yok.");
  assert(
    html.indexOf('<section id="rooms"') > html.indexOf('<section id="devices"'),
    "Odalar gorunumu ana ekran diliminin icinde: butce hesabi bozulur."
  );
  assert(html.includes('id="roomCards"'), "Odalar gorunumunde kart kabi (#roomCards) yok.");
  const roomGrid = squeeze(
    requireValue(rules, ".room-card-grid", "grid-template-columns", railViewport, "Oda kart izgarasi")
  );
  assert(
    roomGrid.includes("minmax(") && /clamp\(|vw|%/.test(roomGrid),
    `Oda kart izgarasi sabit sutun: ${roomGrid}`
  );

  /* 12e) PANO DUZENLEME EV MODUNDAN CEKILDI — ama KOD SILINMEDI (geri donus yolu).
     Iki dugme de isaretlemede DURMALI ve `hidden` olmali. Silinirlerse `50-widgets.js`in
     etiket/sinif yazan satirlari ilk turda patlar. */
  for (const id of ["addWidget", "editDashboard"]) {
    const at = html.indexOf(`id="${id}"`);
    assert(at >= 0, `Emekli edilen dugme isaretlemeden SILINMIS: #${id} (kod silinmeyecekti).`);
    const tag = html.slice(html.lastIndexOf("<button", at), html.indexOf(">", at));
    assert(tag.includes("hidden"), `#${id} hala ev modunun basliginda gorunuyor: pano duzenleme emekli.`);
  }

  /* 12f) GOZ VE YILDIZ ULASILABILIR KALMALI. Ikisi de bugune kadar YALNIZ duzenleme kipinde
     goruntuleniyordu; kip ev modundan cekildigine gore kalici yerleri Odalar gorunumudur.
     Bu madde olmasa "kart gorunurlugu KALIR" karari sessizce olur bir ozellige donerdi. */
  for (const selector of [".room-card .tile-eye", ".room-card .tile-star"]) {
    assert.equal(
      declaredValue(rules, selector, "display", railViewport),
      "grid",
      `\`${selector}\` gorunmuyor: cihaz gorunurlugu ulasilamaz hale gelmis.`
    );
  }

  /* 12f-2) DUZENLEME IKONLARI ANA EKRANDA BASILMAZ — ama KOD SILINMEDI.
     Kullanicinin karari: "kalksin ama belki sonra kullaniriz." Iki sart birlikte denetlenir,
     cunku tek basina her biri yanlis yone kayar: yalnizca "gorunmesin" deseydik biri dosyayi
     silerek de gecerdi; yalnizca "kod dursun" deseydik ikonlar ana ekranda kalirdi. */
  const homeSuppressed = [
    ".widget-edit-controls",
    ".tile-width-toggle",
    ".tile-eye",
    ".tile-star"
  ];
  for (const selector of homeSuppressed) {
    assert.equal(
      declaredValue(rules, `body[data-active-view="home"] #home ${selector}`, "display", railViewport),
      "none",
      `\`${selector}\` ana ekranda hala basiliyor: pano duzenleme ikonlari ev modundan cekildi.`
    );
  }
  for (const [needle, label, file] of [
    ["setDashboardEditing", "pano duzenleme kipi", widgets],
    ["data-widget-move", "kart tasima dugmeleri", html],
    ["data-widget-remove", "kart kaldirma dugmesi", html],
    ["tile-width-toggle", "doseme genisligi dugmesi", homeJs],
    ["tileVisibilityHtml", "doseme gozu", homeJs],
    ["tileFavoriteHtml", "doseme yildizi", homeJs]
  ]) {
    assert(file.includes(needle), `Emekli edilen ozellik KODDAN SILINMIS: ${label} (${needle}).`);
  }

  /* 12q) SOL RAY TAM BOY. Kullanicinin ilk sikayeti: ray ekranin altina kadar inmiyordu.
     Uc sart birlikte: kabuk en az bir ekran boyu, ray o boyun tamami, Profil en altta.
     Olcu `dvh` — tablet adres cubugu `vh`de yanlis sonuc veriyor. */
  assert.equal(
    declaredValue(rules, ".side-rail", "height", { width: 1024, height: 640 }),
    "100dvh",
    "Sol ray tam boy degil: `height:100dvh` bekleniyor."
  );
  for (const selector of [".shell"]) {
    const shellHeights = rulesFor(rules, selector, railViewport)
      .flatMap((rule) => declarations(rule.body))
      .filter((entry) => entry.name === "min-height")
      .map((entry) => squeeze(entry.value));
    assert(shellHeights.length > 0, "Kabugun (.shell) en az bir ekran boyu bildirimi yok.");
    assert(
      shellHeights.every((value) => value === "100dvh"),
      `Kabuk hala \`vh\` ile olculuyor: ${shellHeights.join(" / ")}`
    );
  }
  assert.equal(
    declaredValue(rules, ".rail-bottom", "margin-top", railViewport),
    "auto",
    "Profil rayin en altina yapismiyor (`margin-top:auto` yok)."
  );

  /* 12r) "EVIN DURUMU" UZUN KARTI ANA EKRANDAN KALKTI, OZETI BASLIKTAKI HAPA DONDU.
     Kart emekli ama SILIK DEGIL: isaretlemesi, katalog tanimi ve locale anahtarlari yerinde. */
  assert(
    /const\s+retiredHomeWidgets\s*=\s*new Set\(\[([^\]]*)\]\)/.test(coreJs),
    "10-core.js icinde `retiredHomeWidgets` tanimi yok: emekli kart yine ana ekrana girer."
  );
  assert(
    coreJs.match(/const\s+retiredHomeWidgets\s*=\s*new Set\(\[([^\]]*)\]\)/)[1].includes('"summary"'),
    "\"Evin durumu\" karti hala ana ekranin izgarasina giriyor."
  );
  assert(
    widgets.includes("retiredHomeWidgets.has(id)"),
    "50-widgets.js emekli kart listesini kullanmiyor: kart yine basilir."
  );
  assert(
    /defaultDashboardWidgets\s*=\s*\["quick"\]/.test(coreJs),
    "Varsayilan pano duzeni hala uzun bilgi karti aciyor."
  );
  assert(
    html.includes('data-widget="summary"') && html.includes('data-i18n="summaryWidget"'),
    "Emekli edilen \"Evin durumu\" karti isaretlemeden SILINMIS (kod silinmeyecekti)."
  );
  assert(
    html.includes('id="homeHealth"') && html.includes('class="status-dot"'),
    "Baslikta evin sagligi hapi (#homeHealth + .status-dot) yok."
  );
  const healthAt = head.indexOf('id="homeHealth"');
  assert(healthAt >= 0, "Saglik hapi ana ekran basliginda degil.");
  assert(
    head.indexOf('class="home-status"') < healthAt,
    "Saglik hapi basligin sag bolumunun (home-status) disinda."
  );
  assert(
    homeJs.includes("genHomeHealthModel(") && /function\s+renderHomeHealth\s*\(/.test(homeJs),
    "Hapin metni tek kaynaktan (genHomeHealthModel) yazilmiyor: ikinci bir saglik kurali acilmis."
  );
  const healthPill = evaluateLength(
    requireValue(rules, ".home-health", "min-height", railViewport, "Ev sagligi hapi"),
    { viewport: railViewport, vars: {} }
  );
  assert(healthPill >= 42, `Ev sagligi hapi ${Math.round(healthPill)}px: demo 42px istiyor.`);
  /* 1024x640'ta basligin sag hucresine ~260px dusuyor: uc gosterge + YAZILI hap oraya sigmaz ve
     satir sarar — sarma basligi bir kademe uzatir, butce de o kademeyi saymaz. Bu genislikte hap
     yalniz NOKTA olmali; cumle `title`/`aria-label`da durur (bkz. renderHomeHealth). */
  assert.equal(
    declaredValue(rules, ".home-health", "width", railViewport),
    "42px",
    "1024x640'ta saglik hapi hala yazili: baslik sarar, butce yalan soyler."
  );
  assert.equal(
    declaredValue(rules, ".home-health .home-health-text", "display", railViewport),
    "none",
    "Dar baslikta hapin yazisi hala ciziliyor."
  );
  assert(
    /aria-label/.test(homeJs.slice(homeJs.indexOf("function renderHomeHealth"), homeJs.indexOf("function renderHomeSummary"))),
    "Hapin cumlesi ekran okuyucuya verilmiyor: yazi gizlenince bilgi tamamen kayboldu."
  );
  assert(
    !flat.includes("color-mix("),
    "Panelde `color-mix()` var: eski Android WebView'da cozulmez."
  );

  /* 12s) FAVORILER ODA IZGARASINDAN ONCEKI BOLUM — dis kart DEGIL.
     Bolum yalniz yildizli cihaz varsa basilir; sira listesinden yeri gelmez. */
  assert(
    widgets.includes("placeNode(rail,favoritesWidget,rail.firstElementChild)"),
    "Favoriler bolumu oda izgarasindan once sabitlenmemis."
  );
  assert(
    /if\(favoritesWidget&&favoriteEntries\(\)\.length\)/.test(widgets),
    "Favori yokken de Favoriler bolumu basiliyor."
  );
  assert(
    html.includes("favorites-widget") && html.includes('data-widget="favorites"'),
    "Favoriler bolumunun iskeleti isaretlemeden kalkmis."
  );
  assert(
    /class="dashboard-widget favorites-widget"/.test(html) &&
      /class="home-section-head" data-home-section="favorites"/.test(html),
    "Favoriler dis kart olmadan ortak bolum basligini kullanmiyor."
  );
  assert(
    !/class="[^"]*widget-card[^"]*favorites-widget|class="[^"]*favorites-widget[^"]*widget-card/.test(html),
    "Favoriler bolumu hala dis kart (`widget-card`) tasiyor."
  );

  /* 12g) ODANIN ANA ANAHTARI AYRI VE DOKUNULABILIR (not §6.3 + §7). Kart govdesi odaya girer,
     anahtar ayri hedeftir; ikisi de 44px'in altina inemez. Hap bicimli anahtarlar kare ikon
     olcusune (`.group-widget-actions button`) sikismamali. */
  const masterMin = evaluateLength(
    requireValue(rules, ".room-master", "min-height", railViewport, "Odanin ana anahtari"),
    { viewport: railViewport, vars: {} }
  );
  assert(masterMin >= TOUCH_TARGET, `Odanin ana anahtari ${masterMin}px: dokunma hedefi 44px'in altinda.`);
  for (const selector of [".group-widget-actions .ov-switch", ".group-widget-actions .room-master"]) {
    assert.equal(
      declaredValue(rules, selector, "width", railViewport),
      "auto",
      `\`${selector}\` kare ikon olcusune sikisiyor: \`.group-widget-actions button\` geri alinmali.`
    );
  }
  assert(
    /data-room-master=/.test(homeJs),
    "Odanin ana aç/kapat eylemi yok: oda karti sozlesmesinin dorduncu maddesi eksik."
  );
  assert(
    homeJs.includes("roomMasterKinds"),
    "Ana anahtarin hedef kumesi yazili degil: kilit/perde toplu surulme riski."
  );

  /* 12h) YONETICI GEZINMESI (Asama 6, not §4.2): Genel Bakis · Cihazlar · Otomasyonlar ·
     Baglantilar · Sistem. Dordu yonetici kisitli; "Cihazlar" IKI modda da AYNI satirdir —
     yonetici icin ikinci bir kopya acilirsa "aktif satir" iki yerden yazilir ve kacinilir. */
  for (const view of ["adminOverview", "automations", "connections", "settings"]) {
    const at = rail.indexOf(`data-view="${view}"`);
    assert(at >= 0, `Sol rayda yonetici satiri yok: ${view}.`);
    const tag = rail.slice(rail.lastIndexOf("<button", at), rail.indexOf(">", at));
    assert(
      tag.includes("nav-button") && tag.includes("rail-button"),
      `Yonetici ray satiri ortak gezinme sinifini tasimiyor (${view}): ikinci bir mekanizma acilmis.`
    );
    assert(
      tag.includes("data-admin-only"),
      `Yonetici satiri ev modunda da ciziliyor: ${view}.`
    );
  }
  assert.equal(
    rail.split('data-view="devices"').length - 1,
    1,
    "Rayda ikinci bir Cihazlar satiri var: yonetici icin ayri kopya acilmis."
  );
  assert(
    rail.indexOf('data-view="devices"') < rail.indexOf('data-view="adminOverview"'),
    "Genel Bakis, ev modu satirlarinin arasina girmis."
  );
  assert(
    rail.indexOf('data-view="adminOverview"') < rail.indexOf('data-view="automations"'),
    "Genel Bakis yonetici satirlarinin basinda degil."
  );
  assert(
    rail.indexOf('data-view="settings"') > rail.indexOf('data-view="connections"'),
    "Sistem satiri yonetici gezinmesinin sonunda degil."
  );
  assert(
    /data-view="settings"[\s\S]{0,600}data-i18n="navSystem"/.test(rail),
    "Sistem satiri hala \"Ayarlar\" diyor: yonetici gezinmesinin dili tek olmali."
  );

  /* 12i) GENEL BAKIS GORUNUMU ANA EKRANIN DISINDA, YONETICI KISITLI VE UC KABI YERINDE.
     Icerideyse 1024x640 butcesi yalan soyler (bkz. 12d, Odalar). */
  assert(
    html.includes('<section id="adminOverview" class="view" data-admin-only>'),
    "Genel Bakis gorunumu (#adminOverview) yok ya da yonetici kisitli degil."
  );
  assert(
    html.indexOf('<section id="adminOverview"') > html.indexOf('<section id="devices"'),
    "Genel Bakis ana ekran diliminin icinde: butce hesabi bozulur."
  );
  for (const [id, label] of [
    ["adminOverviewStats", "sayi doselemeleri"],
    ["adminServiceList", "servis sagligi"],
    ["adminAttentionList", "dikkat isteyenler"]
  ]) {
    assert(html.includes(`id="${id}"`), `Genel Bakis'ta ${label} kabi yok: #${id}.`);
  }

  /* 12j) GENEL BAKIS BES SEYI DE GOSTERIR VE SAYIYI KENDI UYDURMAZ: cihaz sayisi, cevrimici,
     otomasyon, dusuk pil, servis sagligi. Cihaz tarafinin tek dogrusu `genHomeHealthModel`;
     otomasyon sayisi `state.automations`in tamamidir. `genSceneCatalog` yalniz elle calisan hizli
     sahneleri suzdugu icin burada kullanilirsa otomatik kurallar eksik sayilir. */
  const overviewJs = await readFile(path.join(projectRoot, "public", "js", "72-admin-overview.js"), "utf8");
  for (const [needle, label] of [
    ["genHomeHealthModel(", "cihaz sagligi modeli"],
    ["state.automations", "tum otomasyon kayitlari"],
    ["adminStatDevices", "cihaz sayisi"],
    ["adminStatOnline", "cevrimici"],
    ["adminStatAutomations", "otomasyon"],
    ["adminStatLowBattery", "dusuk pil"],
    ["adminServiceMqtt", "servis sagligi"]
  ]) {
    assert(overviewJs.includes(needle), `Genel Bakis eksik: ${label} (${needle}).`);
  }
  assert(
    overviewJs.includes("adminServiceUnknown"),
    "Okunmamis servis \"bilinmiyor\" demiyor: yalan bir saglik isareti basilir."
  );

  /* 12k) YENI PANEL DOSYASI UC YERDE BIRDEN KAYITLI: diskte, `index.html`'de ve `src/index.ts`
     icindeki `panelAssetRoutes` tablosunda. Ucu birlikte guncellenmezse dosya ya hic yuklenmez
     ya da 404 doner (panel-graph.mjs ilk ikisini goruyor, ucuncusunu bu madde tutuyor). */
  const serverIndex = await readFile(path.join(projectRoot, "src", "index.ts"), "utf8");
  assert(
    html.includes('<script src="/js/72-admin-overview.js"></script>'),
    "index.html'de 72-admin-overview.js etiketi yok."
  );
  assert(
    serverIndex.includes('"/js/72-admin-overview.js"'),
    "src/index.ts icindeki panelAssetRoutes tablosunda 72-admin-overview.js yok: dosya 404 doner."
  );

  /* 12l) YÖNETİCİ KABUĞU TEK KAYNAKTIR. PIN uyarısı bütün görünümlerin dışında sabitlenir;
     "Ev moduna dön" ise büyük kartın içinde değil, sol rayda bağımsız bir düğmedir. */
  assert.equal(
    html.split('id="adminModeStrip"').length - 1,
    1,
    "Yonetici uyari kabi ikinci kez kurulmus."
  );
  const stripAt = html.indexOf('id="adminModeStrip"');
  assert(
    stripAt > html.indexOf("<main>") && stripAt < html.indexOf('<section id="home"'),
    "PIN uyari kabi bir gorunumun icine tasinmis: yalniz o ekranda gorunur."
  );
  assert(html.indexOf('id="defaultPinBanner"') > stripAt, "Varsayilan PIN uyarisi yonetici kabinin icinde degil.");
  const railNavAt = html.indexOf('class="rail-nav"');
  assert(
    html.indexOf('id="leaveAdminModeButton"') > railNavAt && html.indexOf('id="leaveAdminModeButton"') < html.indexOf('data-view="home"'),
    "Gorunur Home dugmesi sol gezinmenin en basinda degil."
  );
  const stripTag = html.slice(html.lastIndexOf("<div", stripAt), html.indexOf(">", stripAt));
  assert(stripTag.includes("data-admin-only"), "Mod seridi ev modunda da ciziliyor.");

  /* 12m) SISTEM OZET + SAG CEKMECE DESENIDIR. Eski iki panel form dugumlerinin park yeridir;
     kaybolurlarsa mevcut ayar baglantilari patlar. Kullanici ise alti ozet kartindan birine
     dokunur ve her hedef TEK, gercek bir DOM kimligine cozulur. */
  for (const id of ["settingsTabUsage", "settingsTabSetup", "settingsPanelUsage", "settingsPanelSetup"]) {
    assert(html.includes(`id="${id}"`), `Ayarlar'in korunmasi gereken park parcasi kaybolmus: #${id}.`);
  }
  assert(html.includes('id="systemDetailDialog"'), "Sistem ayrinti cekmecesi kaybolmus.");
  const systemTargetGroups = [...html.matchAll(/data-system-targets="([^"]+)"/g)];
  assert.equal(systemTargetGroups.length, 6, "Sistem merkezindeki alti ozet kartinin hedef grubu eksik.");
  const systemTargets = systemTargetGroups.flatMap(([, value]) => value.split(",").map((id) => id.trim()).filter(Boolean));
  assert.equal(new Set(systemTargets).size, systemTargets.length, "Bir ayar karti iki sistem cekmecesine birden baglanmis.");
  for (const id of systemTargets) {
    assert(new RegExp(`id=["']${id}["']`).test(html), `Sistem cekmecesi olmayan hedefe gidiyor: #${id}.`);
  }

  /* 12n) RISKLI ISLEMLER ONAYSIZ CALISMAZ. Belirtecler sunucunun sozlesmesidir; biri dususe
     islem tek dokunusla, onaysiz calisir hale gelir. */
  const settingsJs = await readFile(path.join(projectRoot, "public", "js", "70-settings.js"), "utf8");
  const zigbeeJs = await readFile(path.join(projectRoot, "public", "js", "80-zigbee-tools.js"), "utf8");
  const guarded = `${settingsJs}\n${zigbeeJs}`;
  for (const token of ["APPLY", "RESTART", "RESTORE", "CHANGE"]) {
    assert(
      guarded.includes(`"${token}"`),
      `Riskli islemin onay belirteci kaybolmus: ${token}.`
    );
  }

  /* 12p) YONETICI EKRANLARINDA 44px KURALI (not §7). Taban dugme bicimi dolgudan 43px'e
     dusuyordu; kurulum ekranlarinin dugmeleri dokunma hedefinin altina inemez. Kural yonetici
     gorunumlerine baglidir, ana ekranin butcesine dokunmaz. */
  for (const selector of [
    "#adminOverview .secondary",
    "#automations .secondary",
    "#connections .secondary",
    "#settings .secondary",
    "#settings .danger-button"
  ]) {
    const buttonMin = evaluateLength(
      requireValue(rules, selector, "min-height", railViewport, `Yonetici dugmesi (${selector})`),
      { viewport: railViewport, vars: {} }
    );
    assert(buttonMin >= TOUCH_TARGET, `\`${selector}\` ${buttonMin}px: dokunma hedefi 44px'in altinda.`);
  }

  /* 12o) GENEL BAKIS'IN OLCULERI: 44px dokunma hedefi ve AKISKAN izgara (sabit sutun yok). */
  for (const selector of [".admin-service-row", ".admin-attention-row"]) {
    const rowMin = evaluateLength(
      requireValue(rules, selector, "min-height", railViewport, `Genel Bakis satiri (${selector})`),
      { viewport: railViewport, vars: {} }
    );
    assert(rowMin >= TOUCH_TARGET, `\`${selector}\` ${rowMin}px: dokunma hedefi 44px'in altinda.`);
  }
  for (const [selector, label] of [
    [".admin-stat-grid", "Genel Bakis sayi izgarasi"],
    [".admin-overview-columns", "Genel Bakis sutunlari"]
  ]) {
    const columns = squeeze(requireValue(rules, selector, "grid-template-columns", railViewport, label));
    assert(
      columns.includes("minmax(") && /clamp\(|vw|%/.test(columns),
      `${label} sabit sutun: ${columns}`
    );
  }

  return {
    head: Math.round(headHeight),
    strip: [mainPadTop, systemBarPadding, pinBarPadding, stackedBarPadding].map((value) => Math.round(value)),
    board: Math.round(VIEWPORT.height - fixed - stackedBarPadding)
  };
}

/** `:root` ve `#home` gibi yerlerde tanimli uzunluk degiskenleri (goruntu alanina gore son hali). */
function collectVariables(rules, viewport) {
  const vars = {};
  for (const rule of rules) {
    if (rule.media.some((condition) => !mediaApplies(condition, viewport))) continue;
    for (const entry of declarations(rule.body)) {
      if (entry.name.startsWith("--")) vars[entry.name] = entry.value;
    }
  }
  return vars;
}

/** `start` isaretinden `end` isaretine kadarki dilim (bitis DISARIDA kalir). */
function between(html, start, end) {
  const from = html.indexOf(start);
  assert(from >= 0, `Isaretlemede bulunamadi: ${start}`);
  const to = html.indexOf(end, from);
  assert(to >= 0, `Isaretlemede bulunamadi: ${end}`);
  return html.slice(from, to);
}

/** `start` ile `end` arasindaki HTML dilimi (ilk eslesme). */
function section(html, start, end) {
  const from = html.indexOf(start);
  assert(from >= 0, `Isaretlemede bulunamadi: ${start}`);
  const to = html.indexOf(end, from);
  assert(to >= 0, `Kapanmayan blok: ${start}`);
  return html.slice(from, to + end.length);
}
