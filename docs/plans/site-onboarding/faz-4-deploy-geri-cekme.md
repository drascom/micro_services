# Faz 4: deploy.sh, sunucuda eklenen siteleri yerele geri çekme

Sen bir worker'sın. Önce `docs/plans/site-onboarding/README.md` dosyasındaki ortak kuralları oku ve uygula.
Çıktın kısa olsun, özet geç. Bağımsız bir faz; diğerleriyle paralel yürüyebilir.

## Amaç
Admin panelinden sunucuda (61) eklenen yeni site config'leri (`<site>.yaml`, `.baseline.json`, `.vN.yaml`
arşivleri) yerel `server/app/scraper/configs/` aynasına **otomatik** gelsin. Böylece yerel ayna gerçekten ayna olur
ve sonraki deploy bu siteleri "yerelde yok" diye karıştırmaz.

İlke (CLAUDE.md): yerel `configs/` sunucunun aynasıdır. **Çekme yönü yalnızca sunucu → yerel.** Var olan sitelerin
korunma ve ezme mantığı DEĞİŞMEZ.

## Mevcut durum (önce `deploy.sh`'yi baştan sona oku; config karar mantığı yaklaşık satır 292-830)
- Deploy, sunucu configs'ini önce `deploy-backups/remote-configs-<ts>/configs` altına indirir.
- md5 farkı olan her site için tabloyla karar verir:
  - Sunucu sürümü < yerel ve heal izi yoksa → EZER.
  - Sunucu sürümü ≥ yerel, heal izi var ya da sürüm okunamıyorsa → KORUR (`--exclude`).
- Yerelde olup sunucuda olmayan (LOCALONLY) dosyalar gönderilir.
- Sunucuda olup yerelde olmayan site için şu an yalnızca yedek alınır; yerel aynaya kopyalanmaz.
- Bayraklar: `--dry-run`, `--local-only`, `--keep-configs`, `--overwrite-configs`, `--yes`, `-v` ve diğerleri.

## Yapılacaklar
1. **REMOTEONLY sınıfı:** karar tablosunda "sunucuda var, yerelde yok" siteleri ayrı bir sınıf olarak göster
   (örn. `REMOTEONLY → yerele çekilecek`).
2. **Geri çekme (pull):** deploy akışında, sunucu configs'i indirildikten sonra ve tar gönderiminden ÖNCE
   REMOTEONLY sitelerin dosyalarını indirilen yedekten yerel `configs/`'e kopyala: `<site>.yaml`,
   `<site>.baseline.json`, `<site>.v*.yaml`. Ardından:
   - Bu dosyalar o deploy'un tar'ından `--exclude` edilir (sunucudakiyle zaten aynılar; tekrar göndermek gereksiz).
   - Çıktıda net bir satır yazılır: `"yerele çekildi: <site> (v<N>)"`.
   - `--dry-run`'da kopyalama yapılmaz, yalnızca "çekilecekti" listesi gösterilir. `--local-only` sunucuya
     bağlanmadığı için REMOTEONLY hesaplanamaz; bunu tek satırla bildir.
3. **Var olan sitelerde sunucu daha yeniyse:** bugün KORU kararı veriliyor ama yerel eski kalıyor. Yeni isteğe bağlı
   bayrak `--sync-configs`: KORU kararı verilen sitelerin sunucu dosyalarını yerele kopyalar (aynayı tazeler).
   Varsayılan KAPALI; davranış değişikliği istemiyoruz. `--help` metnine ekle.
4. **Bağımsız komut** `./deploy.sh --pull-configs`: deploy yapmadan, yalnızca sunucu configs'ini indirir, yedekler ve
   REMOTEONLY sitelerini (+ `--sync-configs` ile birlikte verilirse KORU sitelerini) yerele çeker. Restart, tar ya da
   yedek budama yok. `--dry-run` ile birlikte çalışır.
5. **`server/pi/` dizini:** deploy tar'ı `server/` altını gönderiyorsa `server/pi/` (skill + extension) otomatik gider;
   bunu `--local-only -v` ile doğrula. Gitmiyorsa ekle. `server/pi/` için config tarzı koruma YOK; yerel kazanır,
   normal kod gibi.
6. Yerel `deploy-backups/` klasörü commit edilmez (zaten öyle). Çekilen config'ler yerel repoda değişiklik olarak
   görünür; çıktının sonunda "git'te kontrol et ve commit et" ipucu ver.

## Kısıtlar
- Var olan KORU/EZ kurallarının, `--overwrite-configs`/`--keep-configs` anlamlarının ve yedek budamanın davranışı
  değişmez.
- Sunucuya yazma yalnızca normal deploy akışında olur. `--pull-configs` sunucuya HİÇBİR ŞEY yazmaz.
- Gerçek deploy ÇALIŞTIRMA. Sunucuya bağlanan tek izinli deneme: `./deploy.sh --pull-configs --dry-run` ve
  `./deploy.sh --dry-run` (ikisi de yazmaz). Kullanıcının onayı olmadan başka bir mod çalıştırma.

## Testler
- Bu repoda shell testleri yoksa: karar mantığını küçük bir fonksiyona ayırabiliyorsan
  `tests/deploy_configs_test.sh` (yeni, bash) ile sahte `remote/` ve `local/` dizinleri üzerinde REMOTEONLY, KORU,
  EZ ve `--sync-configs` senaryolarını doğrula. Ayıramıyorsan `bash -n deploy.sh` + iki dry-run çıktısını rapora
  koy.
- `shellcheck` varsa çalıştır; yeni uyarı ekleme.

## Kabul kriterleri
- `bash -n deploy.sh` temiz. `--help` güncel. Dry-run çıktısında REMOTEONLY sınıfı görünüyor (sunucuda şu an fazladan
  site yoksa boş liste; bunu belirt).
- Rapor: yeni bayraklar, değişen akış adımları (en fazla 5 madde) ve "CLAUDE.md notu" (Deploy bölümüne eklenecek
  satır).
