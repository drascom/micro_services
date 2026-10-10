# 2026-10-10 sonuçlar — mihuai/turkish-stt vs asistan Whisper

> **Not:** mihu modeli (`models/turkish-stt/`) kullanıcı kararıyla silindi; mihu sonuçları aşağıda tarihsel
> olarak duruyor. Yeniden indirme: `README.md > Kurulum` (git clone + curl). Model yokken `eval.py mihu` atlar.

## large-v3 vs large-v3-turbo, beam vs greedy, CLI vs sunucu (2026-10-10)

> **Karar: v3 + server + greedy** (kullanıcı onayı, 2026-10-10). Asistan: `ggml-large-v3-q5_0` + kalıcı
> `whisper-server` + `whisper_beam_size: 1` (greedy, `-bs 1 -bo 1`; sunucu ve CLI fallback aynı), dil `auto`.
> Turbo modelleri silindi (yeniden: ggml-org/whisper.cpp HF reposu `ggml-large-v3-turbo-q5_0.bin`).

whisper.cpp v1.9.4, Metal, `-t 8 -nt`, şarjda, aynı oturumda sıralı. beam = `-bs 5 -bo 5` (asistan ayarı),
greedy = `-bs 1 -bo 1`. Turbo modelleri: `ggml-large-v3-turbo-q5_0.bin` (574 MB) ve `-q8_0` (874 MB), ggml-org
HF reposu, SHA-256 HF etag ile eşleşti. WER ham / sayı, CER sayı = yalnız TR klipler (user: 24, human: 60 okunan).
EN WER = user'ın 5 İngilizce klibi (sayı-normalize). ms = dosya başı medyan (p95), tüm klipler (user 29, human 90).
CLI = dosya başı süreç (yükleme dahil); sunucu = sıcak `whisper-server`. RSS = tepe, MB.

| model/ayar | set | WER ham / sayı | CER | EN WER | ms/dosya | soğuk ms | RSS |
|---|---|---:|---:|---:|---:|---:|---:|
| v3 q5 beam, CLI (referans) | user | 12,6 / 12,5 | 6,6 | 33,3 | 1938 (2167) | – | 2013 |
| v3 q5 greedy, CLI | user | 12,1 / 11,9 | 6,4 | 33,3 | 1786 (1883) | – | 1520 |
| v3 q5 beam `-l tr`, CLI | user | 12,6 / 12,5 | 6,3 | 100 | 1336 (1519) | – | 2015 |
| turbo q5 beam, CLI | user | 14,9 / 14,2 | 6,9 | 25,6 | 1401 (1452) | – | 826 |
| turbo q5 greedy, CLI | user | 14,9 / 15,3 | 7,5 | 25,6 | 1362 (1386) | – | 753 |
| turbo q5 beam `-l tr`, CLI | user | 14,9 / 14,2 | 6,5 | 33,3 | 842 (888) | – | 826 |
| turbo q8 beam, CLI | user | 15,5 / 14,8 | 7,2 | 28,2 | 1415 (1477) | – | 1132 |
| turbo q8 greedy, CLI | user | 15,5 / 15,9 | 7,6 | 28,2 | 1369 (1391) | – | 1059 |
| v3 q5 beam, sunucu (şu anki asistan) | user | 12,6 / 12,5 | 6,6 | 33,3 | 1570 (1779) | 1851 | 2025 |
| v3 q5 greedy, sunucu | user | 12,1 / 11,9 | 6,4 | 33,3 | 1419 (1507) | 1764 | 1533 |
| turbo q5 beam, sunucu | user | 14,9 / 14,2 | 6,9 | 25,6 | 1182 (1233) | 1409 | 839 |
| turbo q5 greedy, sunucu | user | 14,9 / 15,3 | 7,5 | 25,6 | 1148 (1170) | 1361 | 766 |
| v3 q5 beam, CLI (referans) | human | 8,4 / 6,4 | 2,2 | – | 2175 (3889) | – | 2028 |
| v3 q5 greedy, CLI | human | 8,8 / 6,8 | 2,3 | – | 1880 (2668) | – | 1536 |
| v3 q5 beam `-l tr`, CLI | human | 8,4 / 6,4 | 2,2 | – | 1522 (3243) | – | 2028 |
| turbo q5 beam, CLI | human | 11,2 / 8,6 | 3,0 | – | 1441 (1806) | – | 842 |
| turbo q5 greedy, CLI | human | 12,3 / 9,3 | 3,4 | – | 1380 (1538) | – | 768 |
| turbo q5 beam `-l tr`, CLI | human | 11,2 / 8,6 | 3,0 | – | 885 (1245) | – | 842 |
| turbo q8 beam, CLI | human | 11,9 / 8,9 | 3,2 | – | 1445 (1769) | – | 1152 |
| turbo q8 greedy, CLI | human | 13,2 / 10,0 | 3,6 | – | 1394 (1546) | – | 1077 |
| v3 q5 beam, sunucu (şu anki asistan) | human | 8,4 / 6,4 | 2,2 | – | 1781 (3512) | 2592 | 2053 |
| v3 q5 greedy, sunucu | human | 8,8 / 6,8 | 2,3 | – | 1524 (2306) | 2088 | 1554 |
| turbo q5 beam, sunucu | human | 11,2 / 8,6 | 3,0 | – | 1223 (1584) | 1546 | 858 |
| turbo q5 greedy, sunucu | human | 12,3 / 9,3 | 3,4 | – | 1167 (1323) | 1437 | 767 |

Notlar:
- Önceki tablodaki user WER'i (16,4) 29 klibin tümü (TR+EN) üzerindeydi; burada TR ve EN ayrı.
- EN (5 klip) küçük ve gürültülü: "What is the weather like in London tomorrow?" iki modelde de boş/"." çıkıyor
  (kayıt sorunu olabilir); v3 "Summarize my unread" -> "Summary my iron rate", turbo doğru. EN farkı 1 klipten.
- **Dil tespiti (`-l auto`) ~550–600 ms/dosya**: v3 1938 -> 1336, turbo 1401 -> 842 (user); TR doğruluğu
  değişmiyor, ama `-l tr` İngilizceyi bozuyor (v3 EN WER 100: İngilizceyi Türkçeye çeviriyor). Model ve beam
  dışında en büyük tek maliyet bu.
- **Beam etkisi küçük:** v3'te greedy ~150–300 ms kazandırıyor (p95'te human 3889 -> 2668, beam uzun kliplerde
  pahalı), human WER sayı 6,4 -> 6,8, user TR'de 12,5 -> 11,9 (gürültü düzeyinde). Turbo'da greedy kazancı ~40–60 ms
  ve WER daha çok bozuluyor (8,6 -> 9,3).
- **Turbo:** v3'e göre ~500–700 ms hızlı (CLI), RSS 2,0 GB -> 0,84 GB (q5). Doğruluk kaybı: human WER sayı
  6,4 -> 8,6 (+%34 göreli hata), CER 2,2 -> 3,0; user TR 12,5 -> 14,2. q8 q5'ten iyi değil (gürültü), daha büyük.
- **Turbo'da sunucunun kazancı:** CLI 1401/1441 -> sunucu 1182/1223 ms (~220 ms, %15), sürekli ~0,84 GB.

### Öneri

- Turbo'ya geçmek doğruluk pahasına (~%34 göreli daha çok hata, human'da) ~0,5 s kazandırır; doğruluk
  öncelikse v3 kalmalı. Hız gerekiyorsa en ucuz kazanım sırası: (1) v3 + sunucu + greedy: 1570 -> 1419 (user),
  1781 -> 1524 ms (human, p95 3512 -> 2306), WER ~aynı, RAM 2,0 -> 1,5 GB; (2) dil tespitini hızlandırmak
  (~0,55 s, ör. Türkçe varsayıp düşük güvende `auto`, ya da ayrı hızlı LID) — doğrulanmadı; (3) turbo.
- Turbo seçilirse sunucuyu geri almak gerekmez: ~220 ms kazanç için ~0,84 GB, 10 dk idle timeout ile serbest
  kalıyor. Sunucuyu kaldırmak yalnız RAM'in kritik olduğu durumda mantıklı (turbo + CLI: 1,4 s, boşta 0 GB).

## Kalıcı whisper-server (2026-10-10, uygulandı)

Asistan artık `runtime/whisper/whisper-server` (v1.9.4, aynı kaynak/bayraklar) kullanıyor: ilk kayıtta başlar,
model yüklü kalır, 10 dk boşta kalınca kapanır; hata olursa istek başına tek seferlik `whisper-cli` fallback.
Aynı oturumda sıralı, şarjda. `-l auto -t 8 -nt`, sunucuda `-bo 5 -bs 5` (= CLI varsayılanı).

| yol | set | n | WER ham | WER sayı | CER sayı | ms/dosya medyan (p95) | soğuk ms | tepe RSS |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| whisper-cli (eski, dosya başı süreç) | user | 29 | 16,4 | 16,3 | 10,4 | 1950 (2169) | – (her istek ≈ soğuk) | 2,0 GB |
| **whisper-server (yeni, sıcak)** | user | 29 | 16,4 | 16,3 | 10,4 | **1569 (1784)** | 1851 | 2,0 GB |
| whisper-cli (eski) | human | 60 | 8,4 | 6,4 | 2,2 | 2155 (3872) | – | 2,0 GB |
| **whisper-server (yeni, sıcak)** | human | 60 | 8,4 | 6,4 | 2,2 | **1782 (3524)** | 2539 | 2,0 GB |

- Transkriptler iki yolda **birebir aynı** (user 29/29, human 90/90 klip) — WER farkı yok.
- Kazanç ~380 ms/dosya (%18–20). Kalan süre çözme + `-l auto` dil tespiti (~0,7 s); model yükleme değil.
- Soğuk = süreç başlatma -> ilk metin (sunucu sağlık hazır ~340 ms + ilk istek). Gemma yüklüyken asistan
  içinden soğuk 2095 ms, sıcak 1588 ms.
- RAM: Whisper sunucusu ~2,0 GB RSS (footprint 2,05 GB) sürekli; Gemma llama-server ~7,1 GB RSS (mmap,
  footprint ~0,6 GB) -> ikisi birlikte ~9,1 GB RSS. Boşta 10 dk sonra Whisper serbest kalır (`whisper_idle_ms`).

Makine: Apple M4 Pro (12 çekirdek, 24 GB), şarjda (`pmset`: AC Power, %100), ölçümler sıralı.
mihu: sherpa-onnx 1.13.8, `modified_beam_search`. Whisper: whisper.cpp v1.9.4, `ggml-large-v3-q5_0`, Metal.
Tam döküm (tüm örnekler): `runs/summary.md` (`./run.sh` ile yeniden üretilir).

## Ana tablo

WER/CER: Türkçe normalize (küçük harf, İ/ı, noktalama yok). "ham" = rakamlar normalize edilmeden,
"sayı" = rakam->yazı. ms/dosya = medyan (p95). mihu süresi = sıcak modelde saf hesap;
Whisper süresi = asistandaki gibi süreç + model yükleme dahil (asistan her istekte `whisper-cli` başlatıyor).

| model/ayar | set | n | WER ham | WER sayı | CER sayı | RTF | ms/dosya | tepe RSS | akış: ilk ara / son |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| **whisper-asistan** (`-l auto -t 8`) | human | 60 | 8,4 | **6,4** | **2,2** | 0,240 | 2279 (3925) | 2,0 GB | – (toplu) |
| whisper `-l tr -t 8` | human | 60 | 8,4 | 6,4 | 2,2 | 0,170 | 1510 (3249) | 2,0 GB | – |
| whisper `-l tr -t 4` | human | 60 | 8,4 | 6,4 | 2,2 | 0,170 | 1516 (3271) | 2,0 GB | – |
| **mihu int8, 1 thread** | human | 60 | 17,4 | **17,7** | **6,7** | 0,030 | 193 (837) | 279 MB | 1,21 s / 18 ms |
| mihu int8, 4 thread | human | 60 | 17,4 | 17,7 | 6,6 | 0,031 | 215 (823) | 284 MB | 1,21 s / 17 ms |
| mihu fp32, 1 thread | human | 60 | 17,5 | 17,9 | 6,8 | 0,040 | 261 (1147) | 541 MB | 1,21 s / 25 ms |
| whisper-asistan | say-tr (Yelda) | 24 | 3,4 | 3,4 | 0,9 | 0,538 | 1985 | 2,0 GB | – |
| whisper `-l tr -t 1` | say-tr | 24 | 3,4 | 3,4 | 0,9 | 0,373 | 1396 | 2,0 GB | – |
| whisper `-l tr -t 4 -ng` (CPU) | say-tr | 24 | 3,4 | 3,4 | 0,9 | 0,928 | 3463 | 2,1 GB | – |
| mihu int8, 1 thread | say-tr | 24 | 15,5 | 15,3 | 4,3 | 0,029 | 106 | 292 MB | 0,51 s / 17 ms |
| mihu fp32, 1 thread | say-tr | 24 | 13,8 | 13,1 | 4,0 | 0,042 | 159 | 533 MB | 0,51 s / 25 ms |
| whisper-asistan | mihu-synth* | 81 | 14,2 | 9,4 | 2,3 | 0,558 | 2072 | 2,0 GB | – |
| whisper `-l tr -t 8` | mihu-synth* | 81 | 13,8 | 9,0 | 2,2 | 0,385 | 1437 | 2,0 GB | – |
| mihu int8, 1 thread | mihu-synth* | 81 | 15,4 | 13,1 | 3,3 | 0,029 | 112 | 258 MB | 0,51 s / 17 ms |
| whisper-asistan | say-en | 5 | 5,1 | 5,1 | 5,4 | 0,786 | 1890 | 2,0 GB | – |
| mihu int8, 1 thread | say-en | 5 | 100 | 100 | 63,1 | 0,031 | 74 | 319 MB | – |

\* mihu-synth = modelin kendi yayımladığı sentetik set (yanlı olabilir). Kendi script'leriyle
(`eval/run_eval.py`, 24 kHz + librosa) %15,20 WER / %3,55 CER — ilan edilen 15,37 / 3,57 tekrarlandı.

Soğuk yükleme: mihu ~0,65–0,8 s (bir kez, süreç ömrü boyunca); Whisper `load time` ~0,25 s (sıcak disk
önbelleği) ama asistanda **her istekte** tekrarlanır ve dosya başı süreye dahildir.

## human (read) — konuşmacı bazında, WER/CER (sayı)

| | arda | ayhan | havva | melis | neva |
|---|---:|---:|---:|---:|---:|
| whisper-asistan | 17,9 / 6,3 | 3,6 / 1,6 | 0,9 / 0,1 | 7,1 / 2,0 | 2,7 / 0,8 |
| mihu int8 t1 | 48,2 / 22,4 | 9,8 / 3,0 | 2,7 / 0,3 | 14,3 / 4,1 | 13,4 / 3,7 |

Natural (referanssız) kliplerde de aynı tablo: Whisper metni anlamca doğru ("Bugün günüm çok güzel başladı.
Saat 10'a kadar uyudum…"); mihu akıcı ama kelime düzeyinde daha çok kayıyor ("Bugün benim çok güzel
başladı… kahvaltıma hazırladım"; arda: "Bu müsait… Sposon'a gittik" vs Whisper "Bugün saat… spor salonuna
gittik"; neva: mihu "Cime gittik", Whisper "gym'e gittik").

## Hata örnekleri (kategori bazlı)

mihu int8:
- marka: "Trendyol siparişim…" -> "**Tirangiol** siparişin…"
- özel isim: "Çağlar'a mesaj atmayı" -> "**çağlara rağmen** mesajla atmayı"
- tarih/saat: "3'ünde saat 20:15'te kimle" -> "üçünde saat **2015 Tekim'le**"
- tarih: "hangi güne denk geliyor" -> "hangi gün **neden** geliyor"
- Türkçe karakter (human): "Çağrı… ıhlamur… jilet" -> "**Çağırım**… **ızlamur**… (jilet düştü)"

Whisper (asistan ayarı):
- marka: "Garanti BBVA" -> "garanti **BB veya**"
- sayı: "Faturam 1250 lira" -> "Fatura**n**" (mihu'da da aynı)
- saat: "20:15'te" -> "20.15'te" (biçim; sayı WER'inde ufak ceza)
- human: "jilet" -> "**cilet**", "otobüs yirmi üç dakika gecikti" -> "otobüsü yirmi üç dakika**ya yedik**"
- adres: "kırk iki" -> "42" (rakam yazar; "sayı" normalizasyonunda doğru sayılır)

Sayılar: Whisper rakam yazar ("10'a", "9.30'a"), mihu çoğunlukla yazıyla ("dokuzda", "dokuz otuza") — bu yüzden
human sette Whisper'ın ham WER'i (8,4) sayı-normalize WER'inden (6,4) yüksek.

## İngilizce davranışı

mihu İngilizcede **boş değil, çöp** üretir (Türkçe fonetik uydurma): "Summarize my unread emails from today."
-> `Samris Mihnari Meoz Fırıntı Day`; "Play some relaxing jazz on Spotify." -> `Kaysamıyla laksin Cazonspari Fi`;
"Send a message to John…" -> `Sen de mesaj çıkacağım Z'ye gaybobi lay`. Güven/dil sinyali vermediği için
çıktısından İngilizce olduğu anlaşılamaz. Whisper `-l auto` 5/5 doğru; `-l tr` sabitlenirse 3/5'i Türkçeye
çevirip uyduruyor ("Bugün izlediğim yorumlarımı kısaltıyorum.").

## Hız / bellek notları

- mihu: RTF ~0,03 (M4 Pro, 1 thread) — ilan edilen EPYC 0,128'den hızlı. 4 thread kazanç sağlamıyor (100 ms
  parçalarda iş küçük). Final metin bas-konuş bırakıldıktan **~20 ms** sonra hazır; ilk ara sonuç konuşma
  başladıktan ~0,5 s sonra. RSS ~0,26–0,3 GB (fp32 ~0,54 GB, doğrulukta kazanç yok).
- Whisper: kısa komutta dosya başı ~1,4–2,3 s (30 sn'lik pencere sabit maliyeti + her istekte model yükleme).
  **`-l auto` dil tespiti istek başına ~0,6–0,8 s ekliyor** (2279 vs 1510 ms). Thread sayısı (1/4/8) Metal'de
  etkisiz; Metal kapalı CPU'da 2,5x yavaş. RSS ~2 GB.

## Lisans (mihu Community License 1.0)

Yıllık geliri < 500 bin USD olan için ücretsiz (kişisel, araştırma; ticari kullanım mihu'nun istediği kayıt sonrası).
Zorunlu: "Powered by mihu" ibaresi + NOTICE dosyası. Yasak: çıktılarla rakip model eğitmek, modeli yeniden
dağıtmak/satmak. Eşik aşılınca ayrı sözleşme; ihlalde lisans kendiliğinden sona erer.

## Öneri

**Şu an asistanda Türkçe için mihu'ya geçmek önerilmez.** Gerçek insan sesinde Whisper'ın hatası ~2,8 kat
daha düşük (WER 6,4 vs 17,7; CER 2,2 vs 6,7), marka/özel isimde de mihu daha zayıf ("Trendyol", "Çağlar") —
model kartındaki "brand-aware" iddiası bu sette görülmedi. mihu'nun gerçek avantajı gecikme (final ~20 ms vs
~1,5–2,3 s), bellek (0,3 vs 2 GB) ve akış; ama İngilizcede çöp ürettiği ve dil sinyali vermediği için
"Türkçe -> mihu, İngilizce -> Whisper" yönlendirmesi, dili **önceden** bilmeyi gerektirir (ayrı LID modeli ya da
Whisper'ı yine çalıştırmak), bu da kazancı büyük ölçüde siler.

Daha ucuz ve hemen uygulanabilir kazanım Whisper tarafında: kalıcı whisper sunucusu (istek başına model
yüklememek) ve dil sabitleme/ön-tespit (`-l auto` ~0,7 s).

mihu yine denenecekse (UYGULANMADI), gereken değişiklikler:
1. `src/speech.ts`: STT soyutlaması — `stt_engine: whisper | mihu | hybrid`; mihu için kalıcı Python
   (sherpa-onnx ≥ 1.13.8) işçi süreci (EmaWorker/KokoroWorker kalıbı), stdin'den PCM16, stdout'a metin.
2. Dil yönlendirme: `config.language` sabit `tr` ise mihu; `auto` ise hızlı LID (ör. Whisper tiny/base yalnız
   dil tespiti, ya da mihu ara metninin Türkçe sözlük oranı) — düşük güvende Whisper'a düş.
3. Akış: kayıt sırasında 100 ms parçaları mihu'ya canlı besle (ara sonuç UI'de), bırakınca final ~20 ms.
4. `guardText` eşikleri mihu çıktısına göre gözden geçirilmeli (Whisper'a özgü "altyazı m.k." kalıpları
   mihu'da yok; mihu'nun kısa çöp çıktıları için yeni kural).
5. Lisans: "Powered by mihu" + NOTICE; model dosyaları repoya konmaz (indirme betiği).

Kullanıcı kayıtları (`audio/user/`, `record.py`) eklenince `./run.sh user` ile bu tabloya ayrı satır gelir;
karar o sette de doğrulanmalı.
