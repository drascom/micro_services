# EMA Lightning + Kokoro TTS handoff

Tarih: 9 Ekim 2026

## Durum

Asistan iki yerel birincil TTS motoru kullanır. Entegrasyon çalışma zamanında
başka bir projeye bağlı değildir.

- Türkçe yanıt: EMA Lightning 1.0.4
- İngilizce yanıt: Kokoro-82M FP16, `af_heart` kadın sesi
- EMA hatası: Türkçe Piper DFKI fallback
- Kokoro hatası: İngilizce Piper Alan fallback
- `tts_engine: "piper"`: iki dilde Piper
- `tts_engine: "say"`: eski macOS `say` davranışı

EMA tek konuşmacılıdır. Kokoro çok seslidir fakat bu entegrasyonda klonlama,
duygu komutu veya efekt kontrolü yoktur. `af_heart`, Kokoro model kartında en
yüksek kalite notunu alan sıcak ve nötr Amerikan İngilizcesi kadın sesidir.

## Mimari

`Speech.speak()` önce `spokenText()` filtresini ve yerel dil tespitini çalıştırır.
Motor `ema` iken Türkçe için `EmaWorker`, İngilizce için `KokoroWorker` kullanılır.
İki worker da ilk ilgili yanıtta Python sürecini başlatır ve modeli bellekte
tutar. İstekler stdin/stdout üzerinden satır başına bir JSON mesajıyla taşınır.
EMA 48 kHz, Kokoro 24 kHz PCM16 WAV üretir; geçici dosya `/usr/bin/afplay` ile
oynatılır ve silinir.

İptal sentez sırasında gelirse ilgili worker sonlandırılır; sonraki istek temiz
bir worker başlatır. Oynatma sırasında iptal yalnız `afplay` sürecini durdurur ve
ısınmış worker bellekte kalır. Asistan kapanırken iki worker da kapatılır.

Birincil TTS gerçek bir hata verirse `speechSignal.throwIfAborted()` iptal ile
hatayı ayırır. İptalde fallback yapılmaz. Normal EMA/Kokoro hatasında
`tts_fallback` activity kaydı yazılır ve aynı metin uygun Piper sesiyle üretilir.

## Dosyalar

- `src/ema-worker.ts`: kalıcı EMA worker yaşam döngüsü
- `scripts/ema-worker.py`: EMA modelini bir kez yükleyen sentez worker'ı
- `scripts/ema-requirements.txt`: sabitlenmiş EMA bağımlılıkları
- `src/kokoro-worker.ts`: kalıcı Kokoro worker yaşam döngüsü
- `scripts/kokoro-worker.py`: Kokoro ONNX modelini bir kez yükleyen worker
- `scripts/kokoro-requirements.txt`: sabitlenmiş Kokoro bağımlılıkları
- `src/speech.ts`: dil seçimi, iki birincil yol ve Piper fallback
- `src/config.ts`: TTS ayarları ve doğrulama
- `src/main.ts`: `doctor` çıktısında iki worker ve model kontrolleri
- `src/orchestrator.ts`: kapanışta TTS worker temizliği
- `config.example.json`: varsayılan TTS örneği
- `README.md` ve `ARCHITECTURE.md`: kullanıcı ve mimari belgeleri

Yerel, git dışında tutulan varlıklar:

- `runtime/ema/`: EMA Python 3.12 sanal ortamı
- `models/ema/`: bu servise özel Hugging Face cache'i
- `runtime/kokoro/`: Kokoro Python 3.12 ONNX sanal ortamı
- `models/kokoro/`: FP16 model, ses bankası ve `SOURCES.json`, yaklaşık 192 MB
- `runtime/piper/` ve `models/piper/`: son fallback motorları
- `config.json`: bu makinenin etkin ayarları

## Ayarlar

Varsayılanlar `src/config.ts` içindedir:

```json
{
  "tts_engine": "ema",
  "ema_device": "cpu",
  "ema_speed": 1.0,
  "kokoro_voice": "af_heart",
  "kokoro_speed": 1.0
}
```

`ema_speed` aralığı 0.25–4, `kokoro_speed` aralığı 0.5–2'dir. EMA için bu
Mac'te doğrulanan yol CPU'dur. Kokoro ONNX Runtime ile Apple Silicon CPU'da
çalışır.

## Yeniden kurulum

Repo kökünden:

```bash
uv venv --python 3.12 runtime/ema
uv pip sync --python runtime/ema/bin/python scripts/ema-requirements.txt
uv venv --python 3.12 runtime/kokoro
uv pip sync --python runtime/kokoro/bin/python scripts/kokoro-requirements.txt
```

EMA ilk Türkçe istekte gerekli dosyaları `models/ema/` altına indirir. Kokoro
modeli otomatik indirilmez. `thewh1teagle/kokoro-onnx` GitHub
`model-files-v1.1` sürümünden şu iki dosya `models/kokoro/` altına konur:

```text
kokoro-v1.0.fp16.onnx
SHA-256 f3a290d384fbb27966d462905c71a46cef9e5fd00516b40df32a0b4afe77ac96

voices-v1.0.bin
SHA-256 bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d
```

Kokoro-82M ağırlıkları Apache-2.0, `kokoro-onnx` çalışma zamanı MIT lisanslıdır.

## Doğrulama

Çalıştırılan kontroller:

```bash
npm run check
npm test
./run.sh doctor
```

Gerçek Kokoro worker smoke testinde aynı süreçle iki geçerli mono PCM16 24 kHz
WAV üretildi. Model yüklemesi 0.46 saniye sürdü. 3.69 saniyelik ilk ses 1.00
saniyede, 4.06 saniyelik ikinci ses 0.61 saniyede üretildi; RTF değerleri 0.271
ve 0.151 oldu. Kalite kararı uygulama içindeki gerçek kullanım denemesine
bırakıldı.

- TypeScript kontrolü geçti.
- 50 test geçti.
- Doctor iki TTS worker'ını, iki Kokoro varlığını, Piper modellerini ve
  `afplay` aracını buldu.

## Bilinen sınırlar

- İlk Türkçe yanıt EMA soğuk başlangıcı nedeniyle yaklaşık 10 saniye gecikebilir.
- Kokoro çok kısa İngilizce ifadelerde daha uzun cümlelere göre daha az doğal
  ritim üretebilir.
- EMA/Kokoro fallback olayı UI'da ayrıca gösterilmez; `data/activity.log`
  içinde `tts_fallback` olarak kaydedilir.
- Her worker aynı anda tek sentez işler. Masaüstü konuşma akışı da tek aktif
  konuşma kullandığı için bu mevcut tasarımla uyumludur.
- Model dosyaları ve Python ortamları `.gitignore` kapsamındadır; yeni makinede
  tekrar kurulmalı ve indirilmelidir.

## Sonraki olası işler

- Uygulama açılışından sonra EMA ve Kokoro için arka plan warm-up
- `tts_fallback` olayını native durum metninde görünür kılma
- Uzun yanıtları cümle bazında üretip ilk sesi daha erken oynatma
- Kokoro sesini kullanıcı ayarından seçme
