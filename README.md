# inet-viewer

Yerel ağ ve NetBird cihazlarını tek ekranda birleştiren, salt okunur bir ağ
envanteri ve keşif panelidir. Axum/SQLx backend'i komşu tablosu, mDNS ve
NetBird verilerini toplar; Leptos/WASM arayüzü filtreleme, tarama geçmişi ve
WebSocket üzerinden canlı güncellemeler sunar.

## Özellikler

- `ip neigh`, `avahi-browse` ve `netbird status --detail` sonuçlarını tek cihaz
  kaydında birleştirme
- İsteğe bağlı, sınırlandırılmış aktif IPv4 CIDR keşfi
- SQLite kalıcılığı, otomatik migration ve yeniden başlatma sonrası hızlı açılış
- Arama ve kaynak/durum filtreleri
- Açılışta ve "Şimdi tara" ile tam tarama; arada ~30 sn (gizliyken 2 dk) sessiz durum yenileme (bilinen IP canlılığı + netbird status); tarama ve cihaz durum geçmişi
- WebSocket ile canlı güncellenen Leptos paneli
- Birden fazla açık LAN/NetBird adresinde dinleme
- Tek, kompakt macOS teması (sistem açık/koyu moduna uyar)
- Sertleştirilmiş systemd servisi ve kurulum betiği

## Mimari

Rust workspace üç crate içerir:

| Crate | Görev |
|-------|-------|
| `crates/shared` | Backend ve WASM istemcinin ortak veri/event tipleri |
| `crates/server` | Axum API, SQLx/SQLite repository, keşif sağlayıcıları, tarama zamanlayıcısı ve statik dosya sunumu |
| `crates/web` | Leptos CSR uygulaması; REST başlangıç verisi ve WebSocket güncellemeleri |
| `src-tauri` | macOS için Tauri 2 kabuğu; keşif motoru ve yerel SQLite aynı uygulama sürecindedir |

Sunucu başlangıçta `migrations/` dosyalarını uygular. Keşif sonuçları normalize
edilip birleştirilir, SQLite'a yazılır ve bağlı istemcilere olay olarak yayınlanır.
Frontend ayrı derlenir; release kurulumunda backend `crates/web/dist` çıktısını
statik olarak sunar.

## macOS masaüstü uygulaması

Masaüstü sürümü uzak bir API'ye ihtiyaç duymaz. Tauri kabuğu, Rust keşif
motoru, kullanıcıya özel SQLite veritabanı ve aynı Leptos/WASM arayüzü tek
`.app` içinde çalışır. Veriler macOS uygulama veri dizininde tutulur. Web sürümü
relative REST/WebSocket endpoint'lerini kullanmaya devam eder; masaüstü sürümü
aynı işlemleri sınırlı Tauri IPC komutlarıyla yerel olarak yapar.

### Kurulum ve ilk bağlantı

1. Uygulamayı yerelde derleyip imzalayın (aşağıdaki **Yerel imzalı build** ve
   **Masaüstü geliştirme** bölümleri).
2. DMG'yi açın ve `Inet Viewer.app` dosyasını Applications klasörüne sürükleyin.
3. İlk açılışta **Yerel Ağ** erişimini verin. İzin verilmeden ana panel açılmaz.
   İzin daha önce reddedildiyse Sistem Ayarları > Gizlilik ve Güvenlik > Yerel
   Ağ altında Inet Viewer'ı etkinleştirip uygulamadaki düğmeyi yeniden kullanın.
4. NetBird isteğe bağlıdır. Kurulu ve giriş yapılmışsa uygulama resmî NetBird
   CLI'ın durum bilgisini okur; parola veya token istemez ve saklamaz. NetBird
   olmadan LAN keşfi çalışmaya devam eder.

Yerel Ağ izni, uygulama süreci içinde Network.framework ile istenir ve
doğrulanır: uygulama kendi `_inet-viewer._tcp` Bonjour servisini yayınlar ve
browse sonucunda geri görürse izin açıktır (`NWBrowser`'ın `ready` durumu tek
başına izin anlamına gelmez); `dns-sd`, `ping` veya `arp` child process'lerinin sonucu
TCC izin kararı olarak yorumlanmaz. Masaüstü sürümü ayrıca macOS'un yerleşik `/sbin/route`, `/usr/sbin/ipconfig`,
`/sbin/ping`, `/usr/sbin/arp` ve `/usr/bin/dns-sd` araçlarını mutlak yollarıyla
kullanır. Aktif özel IPv4 arayüzü ve `/24`–`/32` CIDR otomatik belirlenir;
kontrollü ping taraması ARP önbelleğini doldurduktan sonra cihazlar okunur.
Genel veya `/24`'ten geniş ağlar taranmaz. Bonjour başarısız olursa hata
tanılamada ayrı gösterilir ve ARP/NetBird sonuçları korunur.

Web sunucusu ile masaüstü uygulaması aynı veritabanını paylaşmaz. Web paneli
sunucunun kalıcı keşif geçmişini ve sunucunun ağ perspektifini, masaüstü paneli
ise yalnızca ilgili Mac'in yerel SQLite geçmişini ve o Mac'ten görülebilen
cihazları gösterir. Bu nedenle toplam ve çevrimdışı sayıları birebir eşleşmek
zorunda değildir. Masaüstündeki **Keşif Tanılama** bölümü son kaynak başarısını,
hata metnini, arayüz/CIDR'ı ve gözlem sayısını gösterir ve kopyalanabilir.

Yeni build'i kurarken çalışan eski Inet Viewer örneklerini tamamen kapatın,
eski `.app` dosyasını Applications'tan kaldırın ve DMG'deki yeni uygulamayı aynı
konuma kopyalayın. Normal güncellemede TCC kaydını sıfırlamayın. Yalnızca izin
anahtarı görünmesine rağmen bozuk/eski ad-hoc imza kaydı nedeniyle yeni uygulama
prompt göstermiyorsa Terminal'de doğru bundle ID ile şu komutu bir kez çalıştırın:

```bash
tccutil reset LocalNetwork com.drascom.inet-viewer
```

Ardından uygulamayı yeniden açıp **Yerel ağ erişimini iste / tekrar kontrol et**
düğmesini kullanın. Bu işlem Inet Viewer'ın Yerel Ağ kararını siler; diğer
uygulamaların izinlerini etkilemez.

### Ad-hoc paket ve Gatekeeper

Kalıcı bir imza kimliği olmadan üretilen paket ad-hoc imzalıdır ve notarize
edilmez. İlk açılış engellenirse Finder'da uygulamaya Control-tıklayıp **Aç**
seçin veya Sistem Ayarları > Gizlilik ve Güvenlik ekranındaki **Yine de Aç**
düğmesini kullanın. `xattr` korumasını topluca kaldırmak önerilmez.

### Yerel imzalı build (Yerel Ağ izni kalıcı olsun)

Ad-hoc imzanın kimliği her build'de değişen cdhash'tir; macOS eski Yerel Ağ
kararını yeni build'e uygulamaz. Yerel kurulum için keychain'deki kalıcı bir
kimlikle imzalayın (`security find-identity -v -p codesigning`):

```bash
cargo tauri build --bundles app --config \
  '{"bundle":{"macOS":{"signingIdentity":"Apple Development: <ad> (<ID>)"}}}'
```

### Masaüstü geliştirme

macOS üzerinde Rust hedefi, Trunk ve Tauri CLI kurulduktan sonra:

```bash
rustup target add wasm32-unknown-unknown
cargo install trunk --locked --version 0.21.14
cargo install tauri-cli --locked --version '^2.8'
cargo tauri icon src-tauri/app-icon.svg
cargo tauri dev
cargo tauri build --bundles app,dmg
```

Capability yalnızca `core:default` içerir; shell eklentisi veya genel komut
çalıştırma izni verilmez. Keşif komut yolları native kodda sabittir.

## Gereksinimler

- Linux (keşif ve systemd kurulumu için)
- Rust 1.89 veya daha yeni (`rust-toolchain.toml` bu sürümü sabitler)
- `wasm32-unknown-unknown` Rust hedefi
- [Trunk](https://trunkrs.dev/) (WASM paketleme için)
- `iproute2` (`ip`), `iputils` (`ping`) ve `avahi-utils` (`avahi-browse`)
- NetBird cihaz keşfi isteniyorsa `netbird` CLI
- systemd kurulumu için root erişimi; geliştirme için gerekmez

```bash
rustup target add wasm32-unknown-unknown
cargo install trunk --locked
```

## Hızlı çalıştırma

İki terminal kullanın. Önce backend:

```bash
mkdir -p data
DATABASE_URL=sqlite://data/inet-viewer.sqlite \
  cargo run -p inet-viewer-server
```

Ardından frontend geliştirme sunucusu:

```bash
cd crates/web
trunk serve --port 8081 --open
```

Backend durumunu doğrudan kontrol edin:

```bash
curl --fail http://127.0.0.1:8080/api/v1/status
curl --fail http://127.0.0.1:8080/api/v1/devices
```

## Development ve release build

```bash
# Development/native ve doğrudan WASM kontrolleri
cargo build -p inet-viewer-server
cargo build -p inet-viewer-web --target wasm32-unknown-unknown

# Release frontend ve backend
(cd crates/web && trunk build --release)
cargo build --release -p inet-viewer-server
```

Release backend'ini proje içinden çalıştırırken frontend çıktısı varsayılan olarak
`crates/web/dist` altında aranır:

```bash
DATABASE_URL=sqlite://data/inet-viewer.sqlite \
  ./target/release/inet-viewer-server
```

## Yapılandırma

| Değişken | Varsayılan | Açıklama |
|----------|------------|----------|
| `DATABASE_URL` | `sqlite://data/inet-viewer.sqlite` | SQLite bağlantı URL'si; üst dizin önceden var olmalıdır |
| `INET_VIEWER_LISTEN` | `127.0.0.1:8080` | Virgülle ayrılmış açık soket adresleri |
| `INET_VIEWER_ALLOW_WILDCARD` | `false` | Tam olarak `true` ise `0.0.0.0`/`[::]` dinlemeye izin verir |
| `INET_VIEWER_STATIC_DIR` | `crates/web/dist` | Derlenmiş frontend dizini |
| `INET_VIEWER_LAN_CIDR` | ayarsız | Aktif keşif yapılacak özel IPv4 CIDR (`/24`–`/32`) |
| `RUST_LOG` | ortam filtresi | `tracing-subscriber` log filtresi |

Örnek üretim yapılandırması `deploy/inet-viewer.env.example` dosyasındadır.
Gerçek adresleri ve yolları kurulumunuza göre değiştirin; `.env` ve SQLite
verileri git tarafından dışlanır.

## LAN ve NetBird endpoint'leri

Sunucu aynı anda birden fazla açık adrese bağlanabilir. Örneğin LAN adresi
`192.168.0.96`, NetBird adresi `100.111.37.77` ise:

```bash
INET_VIEWER_LISTEN=192.168.0.96:8080,100.111.37.77:8080 \
DATABASE_URL=sqlite://data/inet-viewer.sqlite \
cargo run -p inet-viewer-server
```

Endpoint'ler her iki adreste aynıdır:

```text
http://192.168.0.96:8080/
http://192.168.0.96:8080/api/v1/status
http://192.168.0.96:8080/api/v1/devices
http://100.111.37.77:8080/
http://100.111.37.77:8080/api/v1/status
http://100.111.37.77:8080/api/v1/devices
```

Adresler örnektir. `ip -brief address` ve `netbird status` ile bu makinenin
adreslerini doğrulayın. LAN/NetBird istemcilerinden erişim için güvenlik
duvarında yalnızca güvenilen kaynaklara TCP/8080 açın.

## Aktif CIDR keşfi

`ip neigh` yalnızca çekirdeğin mevcut komşu önbelleğini gösterir. Sessiz LAN
cihazlarını da bulmak için özel IPv4 ağını açıkça verin:

```bash
INET_VIEWER_LAN_CIDR=192.168.0.0/24 \
DATABASE_URL=sqlite://data/inet-viewer.sqlite \
cargo run -p inet-viewer-server
```

Uygulama yalnızca RFC1918 özel IPv4 CIDR'lerini ve `/24`–`/32` aralığını kabul
eder. En fazla 254 adrese, aynı anda en fazla sekiz ICMP isteği gönderir ve sonra
komşu tablosunu okur. Başkasına ait ya da genel ağları taramayın. ICMP engelli
cihazlar görünmeyebilir; aynı LAN'dan trafik üretip taramayı yeniden deneyin.

## Systemd sistem servisi

Önce release frontend ve backend'i derleyin, sonra:

```bash
sudo deploy/install.sh
sudoedit /etc/inet-viewer/inet-viewer.env
sudo systemctl enable --now inet-viewer
systemctl status inet-viewer
```

Betik binary'yi `/usr/local/bin`, frontend'i
`/usr/local/lib/inet-viewer/web`, unit'i `/etc/systemd/system` altına kurar.
SQLite verisi `/var/lib/inet-viewer` altında tutulur. Unit `DynamicUser`, kısıtlı
yazma alanları ve ek systemd sertleştirmeleriyle çalışır. Güncelleme, yedekleme
ve işletim ayrıntıları için [`OPERATIONS.md`](OPERATIONS.md) belgesine bakın.

## Systemd kullanıcı servisi

Root gerektirmeyen tek-kullanıcılı kurulum için önce release build'lerini alın,
sonra dosyaları kullanıcı dizinine kurun:

```bash
install -Dm0755 target/release/inet-viewer-server \
  "$HOME/.local/bin/inet-viewer-server"
mkdir -p "$HOME/.local/lib/inet-viewer" "$HOME/.local/state/inet-viewer" \
  "$HOME/.config/inet-viewer" "$HOME/.config/systemd/user"
cp -R crates/web/dist/. "$HOME/.local/lib/inet-viewer/"
cat > "$HOME/.config/inet-viewer/inet-viewer.env" <<EOF
DATABASE_URL=sqlite://$HOME/.local/state/inet-viewer/inet-viewer.sqlite
INET_VIEWER_STATIC_DIR=$HOME/.local/lib/inet-viewer
INET_VIEWER_LISTEN=127.0.0.1:8080
INET_VIEWER_ALLOW_WILDCARD=false
RUST_LOG=inet_viewer_server=info,tower_http=info
EOF
cat > "$HOME/.config/systemd/user/inet-viewer.service" <<EOF
[Unit]
Description=inet-viewer network inventory dashboard
After=network-online.target

[Service]
EnvironmentFile=$HOME/.config/inet-viewer/inet-viewer.env
ExecStart=$HOME/.local/bin/inet-viewer-server
Restart=on-failure
RestartSec=5
NoNewPrivileges=yes
PrivateTmp=yes

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now inet-viewer
systemctl --user status inet-viewer
```

Servisin kullanıcı oturumu kapalıyken de sürmesi gerekiyorsa yönetici bir kez
`loginctl enable-linger "$USER"` çalıştırabilir. Kullanıcı servisi sistem
unit'indeki `DynamicUser` izolasyonunu sağlamaz; yalnızca güvendiğiniz kullanıcı
hesabında çalıştırın.

## Güvenlik notları

- Uygulamanın kimlik doğrulaması/TLS sonlandırması yoktur; yalnızca güvenilen
  LAN/NetBird ağlarında yayınlayın veya kimlik doğrulamalı ters proxy kullanın.
- Güvenli varsayılan yalnızca `127.0.0.1:8080`'dir. Wildcard bind açıkça
  `INET_VIEWER_ALLOW_WILDCARD=true` olmadan reddedilir.
- İnternet yönlendiricisinde TCP/8080 port yönlendirmesi açmayın.
- CIDR keşfini yalnızca sahibi olduğunuz özel ağlarda kullanın.
- `.env`, veritabanları, loglar, `target/`, `dist/` ve `data/` commit edilmez.
- Sistem servisi ayrıcalıksız `DynamicUser` ile ve salt okunur sistem görünümüyle
  çalışır; gerçek secret'ları örnek env dosyasına yazmayın.

## Testler ve kalite kontrolleri

Proje kökünde tam kontrol seti:

```bash
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
cargo build --release -p inet-viewer-server
cargo build --release -p inet-viewer-web --target wasm32-unknown-unknown
(cd crates/web && trunk build --release)
```

Kurulumdan sonra unit dosyasını `systemd-analyze verify
/etc/systemd/system/inet-viewer.service` ile ayrıca doğrulayabilirsiniz.

Fixture tabanlı parser testleri `fixtures/` içindeki anonimleştirilmiş komut
çıktılarını kullanır; testler gerçek ağı taramaz.

## Sorun giderme

Servis ve loglar:

```bash
systemctl status inet-viewer
journalctl -u inet-viewer -n 100 --no-pager
journalctl -u inet-viewer -f
# kullanıcı servisi için:
systemctl --user status inet-viewer
journalctl --user -u inet-viewer -n 100 --no-pager
```

Yaygın sorunlar:

- `unable to open database file`: SQLite üst dizinini oluşturun ve servis
  kullanıcısının yazabildiğini doğrulayın.
- `address already in use`: `ss -ltnp | grep ':8080'` ile çakışan süreci bulun
  veya `INET_VIEWER_LISTEN` portunu değiştirin.
- wildcard hatası: açık arayüz adresleri kullanın; gerçekten gerekiyorsa ayrıca
  `INET_VIEWER_ALLOW_WILDCARD=true` ayarlayın.
- cihaz eksik: `/usr/bin/ip neigh show`, `/usr/bin/avahi-browse --all --resolve
  --terminate --parsable` ve `/usr/bin/netbird status --detail` komutlarını
  servis kullanıcısının erişimiyle kontrol edin; gerekirse özel CIDR keşfini açın.
- arayüz boş/404: `(cd crates/web && trunk build --release)` çalıştırın ve
  `INET_VIEWER_STATIC_DIR` yolunun `index.html` içerdiğini doğrulayın.
- uzak istemci erişemiyor: dinleme adresini, `ss -ltnp` çıktısını ve host/NetBird
  güvenlik duvarı kurallarını kontrol edin.

Daha ayrıntılı üretim işletimi, güncelleme ve yedekleme adımları
[`OPERATIONS.md`](OPERATIONS.md) içindedir.
