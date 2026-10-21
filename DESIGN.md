# inet-viewer — Tasarım Belgesi

## 1. Amaç

`inet-viewer`, yerel ağdaki ve NetBird overlay ağındaki cihazları tek bir web panelinde gösteren, salt okunur ağ envanteri ve durum izleme uygulamasıdır.

İlk sürüm, `hermes` makinesinde çalışacak ve gerçek verileri aşağıdaki yerel kaynaklardan toplayacaktır:

- `netbird status --detail`
- `ip neigh show`
- `avahi-browse --all --resolve --terminate --parsable`

Uygulama, aynı fiziksel cihazla ilgili farklı kaynaklardan gelen gözlemleri tek bir cihaz kaydında birleştirecektir.

## 2. Mevcut ortam

Geliştirme ve ilk dağıtım hedefi:

| Alan | Değer |
|---|---|
| Makine adı | `hermes` |
| İşletim sistemi | Linux/amd64 |
| Yerel arayüz | `enp1s0` |
| Yerel IP | `192.168.0.96/24` |
| Yerel ağ | `192.168.0.0/24` |
| NetBird arayüzü | `wt0` |
| NetBird IP | `100.111.37.77/16` |
| NetBird FQDN | `hermes.internal` |
| NetBird yönetimi | `https://vpn.drascom.uk:443` |
| NetBird sürümü | `0.81.2` |

İlk incelemede NetBird üzerinden sekiz eş, yerel ağda ise NAS, sunucular, Mac bilgisayarlar, telefonlar, televizyonlar, Sonos cihazları, yazıcı, Zigbee koordinatörü ve ısıtma ağ geçidi gibi farklı cihaz türleri görülebilmiştir.

## 3. Kapsam

### 3.1 MVP kapsamı

- NetBird eşlerini listeleme
- Yerel komşu tablosunu okuma
- mDNS servislerini keşfetme
- Gözlemleri cihaz kayıtlarında birleştirme
- Çevrimiçi, çevrimdışı, boşta ve bağlanıyor durumlarını gösterme
- Yerel ve NetBird IP adreslerini gösterme
- MAC adresi, hostname, model, üretici ve servis bilgilerini gösterme
- Arama, sıralama ve filtreleme
- Son görülme zamanını kaydetme
- Manuel tarama başlatma
- Periyodik otomatik yenileme
- Durum değişikliklerini WebSocket ile tarayıcıya iletme
- Verileri SQLite içinde saklama
- Paneli hem LAN hem NetBird adresinden sunma

### 3.2 MVP dışında kalanlar

- Genel amaçlı port taraması
- Cihazlarda uzaktan komut çalıştırma
- NetBird politika veya grup değiştirme
- Wake-on-LAN
- SSH/RDP oturumu başlatma
- İnternet üzerinden açık erişim
- Çok kullanıcılı yetkilendirme
- SNMP tabanlı ayrıntılı izleme

Bu özellikler daha sonraki sürümlerde ayrıca değerlendirilecektir.

## 4. Teknoloji seçimi

### 4.1 Backend

- Rust
- Tokio async runtime
- Axum HTTP sunucusu
- Serde veri serileştirme
- SQLx ve SQLite
- Tracing yapılandırılmış loglama
- Tower HTTP middleware

### 4.2 Frontend

- Rust
- Leptos CSR
- WebAssembly hedefi: `wasm32-unknown-unknown`
- `wasm-bindgen`
- `web-sys`
- Normal CSS veya küçük bir tasarım token katmanı

### 4.3 Derleme ve paketleme

- Cargo workspace
- Trunk ile frontend geliştirme ve derleme
- Axum tarafından sunulan derlenmiş statik frontend
- İlk aşamada doğrudan native binary
- Kararlı dağıtımda systemd servisi
- İsteğe bağlı sonraki aşamada container imajı

## 5. Mimari

```text
┌──────────────────────────────────────────────┐
│ Tarayıcı                                     │
│ Leptos + WebAssembly                         │
│                                              │
│ • Özet kartları                              │
│ • Birleşik cihaz tablosu                     │
│ • Arama, sıralama ve filtreleme              │
│ • Cihaz ayrıntıları                          │
│ • Canlı durum güncellemeleri                 │
└──────────────────────┬───────────────────────┘
                       │ HTTP API + WebSocket
┌──────────────────────▼───────────────────────┐
│ Axum sunucusu                                │
│                                              │
│ • REST API                                   │
│ • WebSocket olayları                         │
│ • Statik WASM dosyaları                      │
│ • Tarama zamanlayıcısı                       │
│ • Cihaz eşleştirme motoru                    │
│ • SQLite veri katmanı                        │
└────────────┬────────────────┬────────────────┘
             │                │
     ┌───────▼────────┐ ┌─────▼────────────────┐
     │ NetBird kaynağı│ │ Yerel ağ kaynakları  │
     │ CLI, sonra API │ │ ip neigh + mDNS      │
     └────────────────┘ └──────────────────────┘
```

WebAssembly yalnızca kullanıcı arayüzünde çalışır. İşletim sistemi komutları, ağ arayüzleri ve SQLite erişimi native Rust backend tarafından yönetilir.

## 6. Cargo workspace yapısı

```text
inet-viewer/
├── Cargo.toml
├── rust-toolchain.toml
├── README.md
├── DESIGN.md
├── .gitignore
├── crates/
│   ├── server/
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── main.rs
│   │       ├── config.rs
│   │       ├── state.rs
│   │       ├── api/
│   │       │   ├── mod.rs
│   │       │   ├── devices.rs
│   │       │   ├── scan.rs
│   │       │   └── status.rs
│   │       ├── discovery/
│   │       │   ├── mod.rs
│   │       │   ├── netbird.rs
│   │       │   ├── neighbor.rs
│   │       │   └── mdns.rs
│   │       ├── merge/
│   │       │   ├── mod.rs
│   │       │   └── identity.rs
│   │       ├── database/
│   │       │   ├── mod.rs
│   │       │   └── repository.rs
│   │       └── events.rs
│   ├── web/
│   │   ├── Cargo.toml
│   │   ├── index.html
│   │   └── src/
│   │       ├── main.rs
│   │       ├── api.rs
│   │       ├── state.rs
│   │       └── components/
│   │           ├── summary.rs
│   │           ├── filters.rs
│   │           ├── device_table.rs
│   │           └── device_details.rs
│   └── shared/
│       ├── Cargo.toml
│       └── src/
│           ├── lib.rs
│           ├── device.rs
│           ├── observation.rs
│           └── event.rs
├── migrations/
├── static/
├── fixtures/
│   ├── netbird-status.txt
│   ├── ip-neigh.txt
│   └── avahi-browse.txt
└── tests/
```

## 7. Veri modeli

### 7.1 Device

Birleştirilmiş cihaz kaydıdır.

```rust
pub struct Device {
    pub id: DeviceId,
    pub display_name: String,
    pub hostnames: Vec<String>,
    pub lan_ips: Vec<IpAddr>,
    pub netbird_ip: Option<IpAddr>,
    pub netbird_ipv6: Option<IpAddr>,
    pub mac_addresses: Vec<MacAddress>,
    pub manufacturer: Option<String>,
    pub model: Option<String>,
    pub device_type: DeviceType,
    pub status: DeviceStatus,
    pub sources: Vec<DiscoverySourceKind>,
    pub services: Vec<Service>,
    pub first_seen_at: DateTime<Utc>,
    pub last_seen_at: DateTime<Utc>,
}
```

### 7.2 Observation

Her keşif kaynağı önce ortak bir ham gözlem üretir.

```rust
pub struct Observation {
    pub source: DiscoverySourceKind,
    pub source_id: Option<String>,
    pub hostname: Option<String>,
    pub ip_addresses: Vec<IpAddr>,
    pub mac_address: Option<MacAddress>,
    pub manufacturer: Option<String>,
    pub model: Option<String>,
    pub status: ObservationStatus,
    pub services: Vec<Service>,
    pub observed_at: DateTime<Utc>,
    pub attributes: HashMap<String, String>,
}
```

### 7.3 Service

```rust
pub struct Service {
    pub service_type: String,
    pub name: Option<String>,
    pub protocol: TransportProtocol,
    pub port: u16,
    pub hostname: Option<String>,
    pub txt: HashMap<String, String>,
}
```

### 7.4 Durumlar

```rust
pub enum DeviceStatus {
    Online,
    Idle,
    Connecting,
    Offline,
    Unknown,
}
```

Durum önceliği:

```text
Online > Idle > Connecting > Offline > Unknown
```

Bir kaynak cihazı çevrimiçi görüyorsa, eski bir başka kaynaktaki çevrimdışı kayıt cihazı çevrimdışı yapmamalıdır.

## 8. Keşif kaynakları

### 8.1 NetBird

İlk sürüm komutu:

```bash
netbird status --detail
```

Toplanacak alanlar:

- Peer adı/FQDN
- NetBird IPv4 ve IPv6
- Bağlantı durumu
- Bağlantı türü
- Yerel ve uzak uç noktalar
- Son bağlantı güncellemesi
- Son WireGuard handshake
- Gecikme
- Transfer miktarları
- İlan edilen ağlar
- Yerel peer bilgileri
- Yönetim ve signal bağlantı durumu

CLI ayrıştırıcısı saf bir fonksiyon olarak tasarlanmalı ve kayıtlı fixture dosyalarıyla test edilmelidir. İleride NetBird REST API adaptörü aynı trait üzerinden eklenebilmelidir.

### 8.2 Neighbor table

Komut:

```bash
ip neigh show
```

Toplanacak alanlar:

- IP adresi
- Arayüz
- MAC adresi
- Neighbor durumu: `REACHABLE`, `STALE`, `DELAY`, `FAILED` vb.

`STALE`, cihazın kesin olarak çevrimdışı olduğu anlamına gelmez. Bu kayıtlar son görülme kanıtı olarak ele alınmalı, doğrudan çevrimdışı durumuna çevrilmemelidir.

### 8.3 mDNS/DNS-SD

Komut:

```bash
avahi-browse --all --resolve --terminate --parsable
```

Toplanacak alanlar:

- Servis instance adı
- Servis türü
- Hostname
- IPv4/IPv6
- Port
- TXT kayıtları
- Model, üretici, cihaz kimliği ve MAC gibi türetilebilir özellikler

Örnek servis sınıflandırmaları:

| Servis | Cihaz/özellik |
|---|---|
| `_airplay._tcp` | Apple/Samsung/Sonos medya cihazı |
| `_googlecast._tcp` | Chromecast/Android TV |
| `_ipp._tcp` | Yazıcı |
| `_scanner._tcp` | Tarayıcı |
| `_smb._tcp` | NAS/dosya sunucusu |
| `_afpovertcp._tcp` | Apple dosya paylaşımı |
| `_sonos._tcp` | Sonos cihazı |
| `_kdeconnect._udp` | KDE Connect cihazı |
| `_hap._tcp` | HomeKit aksesuarı |
| `_matter._tcp` | Matter cihazı |

## 9. Keşif trait'i

```rust
#[async_trait]
pub trait DiscoveryProvider: Send + Sync {
    fn kind(&self) -> DiscoverySourceKind;
    async fn discover(&self) -> Result<Vec<Observation>, DiscoveryError>;
}
```

Kurallar:

- Shell kullanılmamalı; komutlar `tokio::process::Command` ile doğrudan çalıştırılmalıdır.
- Komut adı ve argümanlar uygulama tarafından sabitlenmelidir.
- Kullanıcı girdisi komut satırına eklenmemelidir.
- Her kaynak için timeout uygulanmalıdır.
- Bir kaynak hata verirse diğer kaynakların sonuçları korunmalıdır.
- Hatalar panelde kaynak sağlığı olarak gösterilmelidir.

## 10. Cihaz kimliği ve birleştirme

Aynı cihaz farklı kaynaklarda farklı adlarla görünebilir. Birleştirme deterministik ve açıklanabilir olmalıdır.

### 10.1 Eşleştirme önceliği

1. NetBird peer ID
2. Normalleştirilmiş global MAC adresi
3. mDNS cihaz UUID/seri numarası
4. Aynı hostname ve örtüşen IP adresi
5. Aynı hostname ve güçlü model/üretici eşleşmesi
6. Manuel eşleştirme kuralı

### 10.2 Birleştirilmemesi gereken durumlar

- Yalnızca aynı üreticiye sahip cihazlar
- Rastgeleleştirilmiş MAC kullanan iki farklı IP
- Sadece benzer görünen hostname değerleri
- Geçmişte aynı DHCP adresini kullanmış farklı cihazlar

### 10.3 Kanıt kaydı

Her alanın kaynağı korunmalıdır:

```text
Device.display_name ← mDNS service name
Device.netbird_ip   ← NetBird
Device.lan_ip       ← ip-neigh + mDNS
Device.model        ← mDNS TXT
Device.status       ← NetBird + neighbor freshness
```

Arayüz, cihazın neden eşleştirildiğini gerektiğinde gösterebilmelidir.

## 11. Veritabanı

Temel tablolar:

```text
devices
observations
device_addresses
device_services
device_sources
scan_runs
source_health
device_events
manual_identity_rules
```

Önerilen saklama yaklaşımı:

- Güncel cihaz durumu sürekli tutulur.
- Ham gözlemler sınırlı süre saklanır.
- Durum değişikliği olayları uzun süre saklanabilir.
- Varsayılan ham gözlem saklama süresi: 7 gün.
- Varsayılan olay saklama süresi: 90 gün.
- Saklama süreleri yapılandırılabilir olmalıdır.

## 12. HTTP API

```text
GET  /api/v1/status
GET  /api/v1/sources
GET  /api/v1/devices
GET  /api/v1/devices/{id}
GET  /api/v1/devices/{id}/events
POST /api/v1/scans
GET  /api/v1/scans/{id}
GET  /api/v1/events/ws
```

### 12.1 Filtre örnekleri

```text
GET /api/v1/devices?status=online
GET /api/v1/devices?source=netbird
GET /api/v1/devices?type=printer
GET /api/v1/devices?q=nas
```

### 12.2 Manuel tarama

`POST /api/v1/scans` yalnızca önceden tanımlı keşif sağlayıcılarını tetikler. İstek içinde komut, IP aralığı veya shell ifadesi kabul edilmez.

Örnek cevap:

```json
{
  "scan_id": "01J...",
  "status": "queued"
}
```

## 13. WebSocket olayları

Olay türleri:

```text
snapshot.updated
device.discovered
device.updated
device.online
device.offline
scan.started
scan.completed
scan.failed
source.health_changed
```

Örnek:

```json
{
  "type": "device.updated",
  "occurred_at": "2026-10-19T12:00:00Z",
  "data": {
    "device_id": "device_...",
    "changed_fields": ["status", "last_seen_at"]
  }
}
```

WebSocket bağlantısı kesilirse frontend artan gecikmeyle yeniden bağlanmalı ve bağlantı kurulduktan sonra tam cihaz listesini yeniden istemelidir.

## 14. Kullanıcı arayüzü

### 14.1 Ana ekran

Üst özet kartları:

- Toplam cihaz
- Çevrimiçi
- Çevrimdışı
- NetBird eşleri
- Yalnızca LAN üzerinde görülenler
- Sorunlu keşif kaynakları

Ana tablo sütunları:

| Sütun | İçerik |
|---|---|
| Durum | Renk ve metin ile durum |
| Cihaz | Görünen ad ve hostname |
| Tür | Sunucu, telefon, TV, IoT vb. |
| Yerel IP | Bir veya daha fazla LAN adresi |
| NetBird IP | Varsa overlay adresi |
| Kaynak | LAN, mDNS, NetBird rozetleri |
| Servisler | Bilinen servis sayısı/ikonları |
| Son görülme | Göreli ve kesin zaman |

### 14.2 Filtreler

- Metin araması
- Durum
- Kaynak
- Cihaz türü
- Yerel/uzak
- NetBird bağlı/bağlı değil
- Yalnızca sorunlu cihazlar

### 14.3 Cihaz ayrıntısı

- Kimlik bilgileri
- Tüm IP ve MAC adresleri
- NetBird bağlantı ayrıntıları
- Keşfedilen servisler
- Kaynak ve eşleştirme kanıtları
- İlk ve son görülme
- Yakın durum geçmişi

### 14.4 Tasarım ilkeleri

- Koyu ve açık tema desteği
- Durum yalnızca renkle anlatılmamalı
- Klavye ile gezinme
- Mobil ve masaüstü uyumu
- Büyük cihaz listelerinde sanallaştırma gerekmiyorsa basit tablo
- Teknik ayrıntılar varsayılan olarak ikincil görünümde
- Hassas kimlik alanları gereksiz yere ana ekranda gösterilmemeli

## 15. Tarama ve zamanlama

Varsayılan program:

| Kaynak | Aralık | Timeout |
|---|---:|---:|
| NetBird | 15 saniye | 10 saniye |
| Neighbor table | 30 saniye | 5 saniye |
| mDNS | 60 saniye | 20 saniye |

Kurallar:

- Aynı kaynak için eşzamanlı iki tarama çalışmamalıdır.
- Manuel tarama mevcut taramayı çoğaltmamalıdır.
- Tarama başarısızlığı var olan cihazları hemen çevrimdışı yapmamalıdır.
- Çevrimdışı kararı kaynak türüne göre belirlenen grace period sonrasında verilmelidir.
- Zamanlayıcı gecikmeleri servis sağlığı metriklerine yazılmalıdır.

## 16. Güvenlik

- Uygulama ilk sürümde salt okunur çalışacaktır.
- Backend komutları shell üzerinden çalıştırılmayacaktır.
- Kullanıcı girdisi işletim sistemi komutlarına aktarılmayacaktır.
- Panel varsayılan olarak yalnızca yapılandırılan LAN ve NetBird adreslerinde dinleyecektir.
- `0.0.0.0` üzerinde dinleme açıkça yapılandırılmalıdır.
- İnternete port yönlendirmesi yapılmamalıdır.
- NetBird API token’ı eklenirse yalnızca backend secret olarak tutulmalıdır.
- Loglarda token, public key, özel cihaz kimliği veya mDNS TXT sırları maskelenmelidir.
- Manuel tarama endpoint'i rate limit ile korunmalıdır.
- State-changing özellikler eklenirse ayrı yetkilendirme gerektirmelidir.
- Frontend hiçbir zaman sistem komutu veya serbest IP aralığı gönderememelidir.

## 17. Yapılandırma

Örnek:

```toml
[server]
listen = "192.168.0.96:8080"
netbird_listen = "100.111.37.77:8080"

[database]
url = "sqlite://data/inet-viewer.sqlite"

[discovery.netbird]
enabled = true
interval_seconds = 15
command = "/usr/bin/netbird"

[discovery.neighbor]
enabled = true
interval_seconds = 30
command = "/usr/bin/ip"
interfaces = ["enp1s0"]

[discovery.mdns]
enabled = true
interval_seconds = 60
command = "/usr/bin/avahi-browse"
interfaces = ["enp1s0"]

[retention]
observations_days = 7
events_days = 90
```

Komut yolları başlangıçta doğrulanmalı; beklenmeyen veya yazılabilir konumlardaki binary'ler için uyarı üretilmelidir.

## 18. Hata yönetimi ve gözlemlenebilirlik

- JSON veya yapılandırılmış text log
- Her tarama için benzersiz `scan_id`
- Kaynak bazında son başarı ve son hata
- Komut exit code, timeout ve ayrıştırma hatalarının ayrılması
- `/api/v1/status` üzerinden genel sağlık
- İsteğe bağlı `/metrics` Prometheus endpoint'i
- Fixture içermeyen bilinmeyen satırlar hata yerine uyarı olarak kaydedilmeli
- Ayrıştırıcı, tek bir bozuk satır nedeniyle tüm çıktıyı reddetmemeli

## 19. Test stratejisi

### 19.1 Birim testleri

- NetBird CLI ayrıştırıcısı
- `ip neigh` ayrıştırıcısı
- Avahi parsable çıktı ayrıştırıcısı
- MAC ve hostname normalizasyonu
- Cihaz eşleştirme öncelikleri
- Durum birleştirme
- Grace period hesapları

### 19.2 Fixture testleri

Gerçek sistem çıktıları hassas alanları maskelenerek `fixtures/` altında tutulacaktır. Ayrıştırıcı değişiklikleri bu fixture'lar üzerinde geriye dönük test edilecektir.

### 19.3 Entegrasyon testleri

- Geçici SQLite veritabanı
- Sahte `DiscoveryProvider` uygulamaları
- API endpoint testleri
- WebSocket olay sıralaması
- Başarısız kaynak durumunda kısmi sonuçların korunması

### 19.4 Uçtan uca test

`hermes` üzerinde doğrulanacak akış:

1. Uygulamayı başlat.
2. `netbird status --detail` sonucunun panele geldiğini doğrula.
3. `ip neigh` cihazlarının göründüğünü doğrula.
4. mDNS cihaz adları ve servislerinin göründüğünü doğrula.
5. `server.internal`, `liv-flow-server.internal` ve `macbook.internal` kayıtlarının LAN gözlemleriyle doğru birleştiğini kontrol et.
6. Bir cihazın erişimi değiştiğinde WebSocket güncellemesini doğrula.
7. Panelin LAN ve NetBird adreslerinden erişilebilirliğini doğrula.

## 20. Geliştirme aşamaları

### Aşama 1 — İskelet

- Cargo workspace
- Shared modeller
- Axum sağlık endpoint'i
- Leptos başlangıç ekranı
- SQLite migration altyapısı

### Aşama 2 — Ayrıştırıcılar

- Gerçek çıktı fixture'ları
- NetBird ayrıştırıcısı
- Neighbor ayrıştırıcısı
- mDNS ayrıştırıcısı
- Birim testleri

### Aşama 3 — Keşif ve birleştirme

- `DiscoveryProvider` uygulamaları
- Zamanlayıcı
- Kimlik/eşleştirme motoru
- SQLite repository
- Kaynak sağlık durumu

### Aşama 4 — API ve panel

- Cihaz endpoint'leri
- Özet kartları
- Cihaz tablosu
- Filtreler
- Ayrıntı görünümü

### Aşama 5 — Canlı güncelleme

- WebSocket event bus
- Frontend yeniden bağlanma
- Manuel tarama
- Durum geçmişi

### Aşama 6 — Dağıtım

- Release build
- systemd unit
- Veri dizini ve izinler
- LAN ve NetBird erişim testi
- Yeniden başlatma ve hata senaryoları

## 21. Kabul kriterleri

MVP aşağıdaki şartlar sağlandığında tamamlanmış sayılır:

- Uygulama Rust native backend ve Rust/WASM frontend olarak derlenir.
- Backend, gerçek üç keşif kaynağını çalıştırabilir.
- Kaynaklardan biri hata verdiğinde diğerleri çalışmaya devam eder.
- NetBird ve LAN gözlemleri tek cihaz kaydında birleştirilebilir.
- Panel cihazları arayabilir ve filtreleyebilir.
- Cihaz durumu ve son görülme zamanı görüntülenir.
- Manuel ve periyodik taramalar çalışır.
- Değişiklikler WebSocket üzerinden canlı görünür.
- SQLite verisi servis yeniden başlatıldığında korunur.
- Panel `hermes` üzerinde LAN ve NetBird üzerinden erişilebilir.
- Ayrıştırıcıların fixture tabanlı testleri geçer.
- Uygulama kullanıcı girdisini shell komutuna dönüştürmez.

## 22. Daha sonraki geliştirmeler

- NetBird REST API entegrasyonu
- NetBird grupları ve erişim politikalarının salt okunur görünümü
- Uzak lokasyonlar için küçük Rust keşif ajanı
- SNMP keşfi
- Router/DHCP lease entegrasyonu
- Bildirimler: Telegram, e-posta, webhook
- Uptime ve gecikme grafikleri
- Manuel cihaz adı, etiket ve eşleştirme düzeltmeleri
- Wake-on-LAN
- Rol tabanlı kullanıcı erişimi
- Prometheus/Grafana entegrasyonu
- İmzalı ve otomatik güncellenebilir binary paketleri

## 23. Temel kararlar

1. Sistem erişimi native Rust backend'de kalacaktır.
2. WebAssembly yalnızca tarayıcı arayüzü için kullanılacaktır.
3. İlk sürüm aktif port taraması yapmayacaktır.
4. NetBird CLI ile başlanacak, API adaptörü sonradan eklenecektir.
5. Keşif kaynakları trait tabanlı ve değiştirilebilir olacaktır.
6. Aynı cihazın birleştirilmesi kanıta dayalı ve açıklanabilir olacaktır.
7. Güvenli varsayılan salt okunur çalışma olacaktır.
8. İlk gerçek geliştirme ve uçtan uca test ortamı `hermes` makinesi olacaktır.
