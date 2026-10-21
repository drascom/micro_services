# inet-viewer kurulum ve işletim kılavuzu

## Release derleme

Geliştirme kullanıcısı olarak proje kökünde:

```bash
rustup target add wasm32-unknown-unknown
cargo install trunk --locked   # sistemde yoksa
(cd crates/web && trunk build --release)
cargo build --release -p inet-viewer-server
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
cargo fmt --all --check
```

Backend statik dosyaları çalışma anında okuduğu için frontend ve backend birlikte kurulmalıdır.

## Kurulum

```bash
sudo deploy/install.sh
sudoedit /etc/inet-viewer/inet-viewer.env
sudo systemctl enable --now inet-viewer
systemctl status inet-viewer
```

Kurulum betiği mevcut `/etc/inet-viewer/inet-viewer.env` dosyasının üzerine yazmaz. Unit, `DynamicUser` ile ayrıcalıksız çalışır; systemd `/var/lib/inet-viewer` ve `/var/log/inet-viewer` dizinlerini otomatik oluşturur ve yalnızca servis kullanıcısına yazma izni verir. Uygulama logları journald'a gider; log dizini gelecekte dosya çıktısı gerekirse güvenli izinlerle hazırdır.

## Ağ erişimi ve güvenli bind

`INET_VIEWER_LISTEN`, virgülle ayrılmış açık soket adresleri kabul eder. Hermes için önerilen değer:

```text
INET_VIEWER_LISTEN=192.168.0.96:8080,100.111.37.77:8080
```

Böylece yalnızca LAN ve NetBird arayüzleri dinlenir. `0.0.0.0` ve `[::]` varsayılan olarak reddedilir; bilinçli olarak tüm arayüzlere açmak gerekirse ayrıca `INET_VIEWER_ALLOW_WILDCARD=true` ayarlanmalıdır. İnternet yönlendiricisinde bu porta yönlendirme açmayın.

Doğrulama:

```bash
ss -ltnp | grep ':8080'
curl --fail http://192.168.0.96:8080/api/v1/status
curl --fail http://100.111.37.77:8080/api/v1/status
```

LAN testi başka bir LAN istemcisinden, NetBird testi başka bir NetBird eşinden de tekrarlanmalıdır. Güvenlik duvarı kullanılıyorsa yalnızca `192.168.0.0/24` ve güvenilen NetBird kaynaklarına TCP/8080 izni verin.

## Güncelleme

Yeni sürümü normal kullanıcıyla derleyin, sonra:

```bash
sudo deploy/install.sh
sudo systemctl restart inet-viewer
curl --fail http://192.168.0.96:8080/api/v1/status
```

SQLite verisi `/var/lib/inet-viewer/inet-viewer.sqlite` altında tutulur ve binary güncellemesi ya da servis yeniden başlatmasında korunur. Güncelleme öncesi yedek:

```bash
sudo systemctl stop inet-viewer
sudo cp -a /var/lib/inet-viewer/inet-viewer.sqlite /var/lib/inet-viewer/inet-viewer.sqlite.backup
sudo systemctl start inet-viewer
```

## İşletim ve sorun giderme

```bash
systemctl status inet-viewer
journalctl -u inet-viewer -n 100 --no-pager
journalctl -u inet-viewer -f
systemctl show inet-viewer -p NRestarts -p ActiveState -p SubState
```

Servis hata ile çıkarsa beş saniye sonra yeniden başlar. Yapılandırma veya bind hatasında restart döngüsünü durdurup nedeni düzeltin:

```bash
sudo systemctl stop inet-viewer
journalctl -u inet-viewer -n 100 --no-pager
sudoedit /etc/inet-viewer/inet-viewer.env
sudo systemctl start inet-viewer
```

Kaynak komutlarını servis ortamından bağımsız kontrol etmek için:

```bash
/usr/bin/netbird status --detail
/usr/bin/ip neigh show
/usr/bin/avahi-browse --all --resolve --terminate --parsable
```

`ip neigh` yalnızca işletim sistemi komşu önbelleğinde bulunan cihazları gösterir. Sessiz
LAN cihazlarını da bulmak için `INET_VIEWER_LAN_CIDR` değerini yerel özel IPv4 ağına
ayarlayın (ör. `192.168.0.0/24`). Uygulama yalnızca bu CIDR içindeki en fazla 254
adrese, aynı anda en fazla sekiz ICMP isteği gönderir ve ardından komşu tablosunu okur.
Güvenlik nedeniyle genel IPv4 alanları ve `/24`'ten geniş ağlar reddedilir.

## Kalıcılık ve restart kontrolü

1. `GET /api/v1/status` ve `GET /api/v1/devices` yanıtlarını kaydedin.
2. `sudo systemctl restart inet-viewer` çalıştırın.
3. Servisin yeniden `active (running)` olduğunu doğrulayın.
4. Aynı endpoint'leri tekrar çağırın ve cihazların SQLite'dan hemen geldiğini doğrulayın.
5. `systemctl is-enabled inet-viewer` çıktısının `enabled` olduğunu kontrol edin; bu, reboot sonrası kalıcılığı sağlar.

Unit değişikliğini kurmadan önce statik doğrulamak için:

```bash
systemd-analyze verify deploy/inet-viewer.service
```
