# MCP entegrasyonu — kendi istemcinizi bağlama

Villa Bridge, ev cihazlarını **Model Context Protocol (MCP)** ile dışarı açar. Bu belge kendi LLM
istemcinizi yazarken gereken her şeyi verir: adres, kimlik, zorunlu başlıklar, kopyala-çalıştır
örnekler ve hata biçimleri.

**Bu belge protokol ve taşıma katmanını anlatır**: adres, kimlik, zorunlu başlıklar, hata
biçimleri. Araçların ne yaptığı, ne beklediği ve ne döndürdüğü — gerçek istek/yanıt örnekleriyle,
modele verilebilecek hazır bir sistem istemiyle birlikte — ayrı bir belgede:
**`docs/mcp-istemci-kilavuzu.md`** (İngilizce, çünkü doğrudan modele gidecek).

Uçta yedi araç var: `list_devices`, `get_device`, `set_device`, `list_automations`,
`get_automation`, `write_automation`, `control_automation`. Yazma araçları **gerçek eve** dokunur;
kilit ve siren ajan yolundan bilerek yazılamaz.

## 1. Uç adresi

```
POST http://192.168.0.91:8091/mcp
```

- Erişim **NetBird mesh** üzerindendir: istemcinin çalıştığı makine zaten peer olduğu için
  sunucunun LAN adresine doğrudan ulaşır. Yeni port açmaya, tünel kurmaya gerek yoktur.
- `home.drascom.uk` arkasındaki PIN kapısı bir **tarayıcı akışıdır**; makine istemcisi oradan
  geçmez ve geçmesine gerek yoktur.
- Protokol sürümü: **`2026-07-28`**. Durumsuzdur — oturum, `Mcp-Session-Id`, GET/SSE akışı,
  `initialize` el sıkışması ve `Last-Event-ID` **yoktur**. Her istek kendi başına doğrulanır.
- `POST` dışındaki bütün yöntemler `405 Method Not Allowed` döner.

## 2. Kimlik: ajan token'ı

1. Panelde **Ayarlar → Asistan erişimi** kartını açın (yalnız yönetici görür).
2. Bir ad yazıp **Token üret** deyin. Ham token **yalnız o an bir kez** gösterilir; kayıtta
   yalnız özeti tutulur, ikinci kez gösterilemez. Kaybederseniz iptal edip yenisini üretin.
3. Token süresizdir, yenilenmez ve çerez taşımaz. İptal ettiğiniz an geçersizleşir.

Her istekte:

```
Authorization: Bearer <token>
```

Uç **çerez oturumunu kabul etmez**: tarayıcıda açık bir Villa Bridge sekmesi bu ucu süremez.

`Origin` başlığı gönderirseniz (yani tarayıcı içinden çağırırsanız) kökeninizin yapılandırmadaki
`mcp.allowedOrigins` listesinde bulunması gerekir; liste boşken Origin gönderen her istek `403`
alır. Origin göndermeyen makine istemcisi (curl, Node, Python) etkilenmez.

## 3. Zorunlu başlıklar

Her `POST` isteğinde aşağıdaki başlıklar bulunmalı **ve gövdedeki karşılığıyla birebir
eşleşmelidir**. Başlık adları büyük/küçük harf duyarsız, değerleri duyarlıdır.

| Başlık | Gövdedeki kaynağı | Ne zaman |
|---|---|---|
| `MCP-Protocol-Version` | `params._meta["io.modelcontextprotocol/protocolVersion"]` | Her istek |
| `Mcp-Method` | `method` | Her istek |
| `Mcp-Name` | `params.name` | Yalnız `tools/call` |

Bunların yanında `params._meta` içinde **başlıkta karşılığı olmayan** bir zorunlu alan daha
vardır: `io.modelcontextprotocol/clientCapabilities` (nesne; yeteneğiniz yoksa `{}` gönderin).

- Eksik ya da uyuşmayan başlık → `400` + JSON-RPC hata kodu **`-32020`**.
- Gövdede zorunlu bir `_meta` alanı eksik ya da tipi yanlış → `400` + **`-32602`**.
- Her başarılı sonuç `result._meta["io.modelcontextprotocol/serverInfo"]` içinde sunucunun
  adını ve sürümünü taşır.
- `Mcp-Name` değeri `=?base64?...?=` (ya da RFC 2047 `=?utf-8?B?...?=`) sentinel biçiminde
  gönderilebilir; sunucu önce çözer, sonra gövdeyle karşılaştırır. ASCII dışı bir ad taşımanız
  gerekmiyorsa düz metin gönderin.

## 4. `tools/list`

Araç kataloğunu ve şemalarını verir. Sıra **belirlenimcidir** (`list_devices`, `get_device`,
`set_device`, `list_automations`, `get_automation`, `write_automation`, `control_automation`),
sayfalama yoktur.

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

Yanıt:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "resultType": "complete",
    "tools": [
      { "name": "list_devices", "title": "…", "description": "…", "inputSchema": {…}, "outputSchema": {…} },
      { "name": "get_device",   "title": "…", "description": "…", "inputSchema": {…}, "outputSchema": {…} },
      { "name": "set_device",   "…": "…" },
      { "name": "list_automations", "…": "…" },
      { "name": "get_automation",   "…": "…" },
      { "name": "write_automation", "…": "…" },
      { "name": "control_automation", "…": "…" }
    ],
    "_meta": {
      "io.modelcontextprotocol/serverInfo": { "name": "villa-bridge", "version": "0.1.0" }
    }
  }
}
```

## 5. `tools/call`

```sh
curl -sS http://192.168.0.91:8091/mcp \
  -H "Authorization: Bearer $VILLA_AGENT_TOKEN" \
  -H "Content-Type: application/json" \
  -H "MCP-Protocol-Version: 2026-07-28" \
  -H "Mcp-Method: tools/call" \
  -H "Mcp-Name: list_devices" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
      "name": "list_devices",
      "arguments": { "room": "salon", "onlineOnly": true },
      "_meta": {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {}
      }
    }
  }'
```

Yanıt:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "resultType": "complete",
    "content": [{ "type": "text", "text": "{\"count\":1,\"devices\":[…]}" }],
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
    "_meta": {
      "io.modelcontextprotocol/serverInfo": { "name": "villa-bridge", "version": "0.1.0" }
    }
  }
}
```

`content[0].text`, `structuredContent`'in JSON serileştirmesinden başka bir şey değildir (geriye
uyum deseni). Modelinize **`structuredContent`'i** verin, metni yalnız yapısal çıktı desteklemeyen
istemcilerde kullanın.

### Node / `fetch` örneği

```js
const endpoint = "http://192.168.0.91:8091/mcp";
const protocolVersion = "2026-07-28";

async function mcp(method, params = {}) {
  const body = {
    jsonrpc: "2.0",
    id: Date.now(),
    method,
    params: {
      ...params,
      _meta: {
        "io.modelcontextprotocol/protocolVersion": protocolVersion,
        "io.modelcontextprotocol/clientCapabilities": {}
      }
    }
  };
  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${process.env.VILLA_AGENT_TOKEN}`,
      "Content-Type": "application/json",
      "MCP-Protocol-Version": protocolVersion,
      "Mcp-Method": method,
      ...(method === "tools/call" ? { "Mcp-Name": params.name } : {})
    },
    body: JSON.stringify(body)
  });
  if (response.status === 202) return null;          // bildirim
  const payload = await response.json();
  if (payload.error) throw new Error(`${payload.error.code}: ${payload.error.message}`);
  return payload.result;
}

const catalog = await mcp("tools/list");
const devices = await mcp("tools/call", { name: "list_devices", arguments: {} });
console.log(devices.structuredContent.devices);
```

## 6. Araçlar

### `list_devices`

Girdi (hepsi opsiyonel): `room` (oda adında geçen metin), `category`
(`light` | `switch` | `cover` | `lock` | `climate` | `fan` | `unknown`), `onlineOnly`, `search`
(ad ya da adres içinde geçen metin).

Çıktı satırı: `id` (IEEE adresi), `name`, `room`, `category`, `availability` ve `primary` —
cihazın **ana kanalının** özeti (`id`, `name`, `kind`, `value`, varsa `unit`). Ana kanal seçimi
panelin kart mantığıyla aynıdır: önce aç/kapa kanalı, yoksa pano kanalı sayılan diğer kumandalar,
o da yoksa ilk kumanda. Kumandası olmayan cihazda `primary: null` döner.

Ham `state` nesnesi bilerek dönmez — model için gürültüdür.

### `get_device`

Girdi: `id` — **IEEE adresi** (`0x…`). Dost isim kabul edilmez; kullanıcı adla sorduysa modeliniz
önce `list_devices` çağırıp adı adrese çevirmelidir (araç açıklamasında da böyle yazar).

Çıktı: `id`, `name`, `room`, `category`, `availability`, `lastSeen`, varsa `linkquality` ve
`powerSource`, ve `controls` dizisi. Her kanal: `id`, `name`, `kind`, `value`, varsa
`min`/`max`/`step`/`unit`/`values`, yalnız yöneticiye açık kanallarda `adminOnly: true`.

### Yazma araçları

`set_device`, `list_automations`, `get_automation`, `write_automation` ve `control_automation`
girdi/çıktı şemalarıyla, gerçek istek ve yanıt örnekleriyle **`docs/mcp-istemci-kilavuzu.md`**
içinde anlatılır. Buradan bilinmesi gereken üç şey:

- `set_device` ve `control_automation`'ın `run`'ı **gerçek cihazlara** dokunur; önizleme yoktur.
- Kilit ve siren ajan yolundan yazılamaz (§8.1 ile aynı gerekçe: onaylayacak insan yok). Panelden
  elle kumanda etkilenmez. Kurulum bilerek açmak isterse `mcp.allowDangerousControls: true`.
- Ajanın yazdığı her kural için `automations.json` önce bir kenara kopyalanır (son 20 yedek) ve
  kural kimin yazdığını taşır. Geri alma panelde durur — model için bir araç değildir.

## 7. `outputSchema`'yı function-calling tanımına çevirmek

`tools/list` yanıtındaki her araç, doğrudan modelinizin araç tanımına dönüşür:

- `name` → fonksiyon adı, `description` → fonksiyon açıklaması (İngilizce yazılmıştır, modelin
  okuması içindir).
- `inputSchema` → fonksiyonun parametre şeması. JSON Schema olduğu gibi geçer; Anthropic
  API'sinde `input_schema`, OpenAI uyumlu API'lerde `parameters` alanına konur.
- `outputSchema` → modelin **beklediği yanıt biçimi**. Çoğu function-calling API'si çıktı şeması
  almaz; şemayı ya sistem istemine kısa bir "araç şu alanları döner" notu olarak koyun ya da
  yanıtı doğrulamak için kullanın (`structuredContent`, şemaya uyar). Şemayı istemci tarafında
  doğrulamak, modelin var olmayan bir alanı uydurduğu durumları erkenden yakalar.
- Katalog belirlenimci sırada ve sabit içerikle döndüğü için prompt önbelleğini bozmaz;
  `tools/list` çıktısını istemcinizde önbelleğe alabilirsiniz.

## 8. Hata biçimleri

Hatalar iki gruptur ve karıştırılmamalıdır:

| Durum | HTTP | JSON-RPC kodu | Anlamı |
|---|---|---|---|
| Token yok / geçersiz / iptal edilmiş | `401` | `40100` | `Authorization: Bearer` başlığını düzeltin. |
| `Origin` izinli değil | `403` | `40300` | Tarayıcıdan çağırıyorsanız kökeni `mcp.allowedOrigins`'e ekleyin. |
| `POST` dışında yöntem | `405` | `40500` | Bu sürümde GET akışı yoktur. |
| Zorunlu başlık eksik ya da gövdeyle uyuşmuyor | `400` | `-32020` | `MCP-Protocol-Version` / `Mcp-Method` / `Mcp-Name`. |
| Desteklenmeyen protokol sürümü (`initialize` dahil) | `400` | `-32022` | `error.data.supported` desteklenen sürümleri listeler. |
| Bozuk JSON-RPC gövdesi | `400` | `-32600` | Tek bir nesne gönderin; toplu (batch) istek yoktur. |
| Bilinmeyen metot | `404` | `-32601` | Bu fazda yalnız `tools/list` ve `tools/call` var. |
| Zorunlu `_meta` alanı eksik ya da hatalı | `400` | `-32602` | `clientCapabilities` gönderin. |
| Bilinmeyen araç adı | `400` | `-32602` | `tools/list` ile katalogu tazeleyin. |
| Bildirim (gövdede `id` yok) | `202` | — | Gövdesiz yanıt döner. |

**Kod aralıkları:** spesifikasyon `-32000`..`-32019` aralığını "legacy" ilan etti; yeni kodlar
JSON-RPC'nin ayrılmış aralığının (`-32768`..`-32000`) **dışında** olmalı. Bu yüzden sunucunun
kendi taşıma katmanı hataları HTTP durum kodu × 100 şemasıyla numaralanır (`401` → `40100`).
Spesifikasyonun tanımladığı kodlar (`-32020` HeaderMismatch, `-32021`
MissingRequiredClientCapability, `-32022` UnsupportedProtocolVersion) olduğu gibi kullanılır.

**Başlık hatası mı, parametre hatası mı?** Başlıkta karşılığı olan bir alan (`protocolVersion`)
gövdede yoksa bu bir başlık↔gövde uyuşmazlığıdır → `-32020`. Başlıkta karşılığı olmayan zorunlu
bir `_meta` alanı (`clientCapabilities`) yoksa ya da tipi yanlışsa → `-32602`.

**Ayrım önemlidir:** yukarıdakiler *yapısal* hatalardır — istemcinin kodunu düzeltmesi gerekir.
Buna karşılık **modelin düzeltebileceği** hatalar (bilinmeyen cihaz adresi, geçersiz argüman,
tanınmayan kategori) JSON-RPC hatası **değildir**: HTTP `200` ile başarılı bir sonuç döner ve
sonucun içinde `isError: true` bulunur:

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "resultType": "complete",
    "content": [{ "type": "text", "text": "No device with address `0x00ff…`. Call `list_devices` …" }],
    "isError": true
  }
}
```

Bu sonucu modele **olduğu gibi geri verin**: mesaj, modelin kendini düzeltebileceği kadar açık
yazılmıştır. `isError: true` sonuçlarında `structuredContent` bulunmaz.

## 9. Hızlı sağlık kontrolü

```sh
# 401 bekleniyor: uç korumasız değil.
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://192.168.0.91:8091/mcp

# 405 bekleniyor: GET akışı yok.
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.0.91:8091/mcp \
  -H "Authorization: Bearer $VILLA_AGENT_TOKEN"
```
