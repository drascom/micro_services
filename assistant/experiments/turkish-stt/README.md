# turkish-stt (mihuai) vs Whisper — Türkçe STT denemesi

Asistanın STT'si (`runtime/whisper/whisper-cli` + `ggml-large-v3-q5_0.bin`) ile
[mihuai/turkish-stt](https://huggingface.co/mihuai/turkish-stt) (66 M parametre, akışlı zipformer2
transducer, ONNX, sherpa-onnx) Türkçe doğruluk, hız ve bellek açısından karşılaştırılır.
Asistan kodu ve bağımlılıkları değiştirilmez; her şey bu klasörde kalır. Sonuçlar: [RESULTS.md](RESULTS.md).

## Kurulum

> mihu modeli silindi (kullanıcı kararı); mihu sonuçları RESULTS.md'de tarihsel. Whisper ölçümü için mihu
> gerekmez: `eval.py mihu` model yoksa mesajla atlar. mihu'yu yeniden ölçmek için aşağıdaki model adımları +
> `uv pip install -p .venv/bin/python "sherpa-onnx==1.13.8"`.

```bash
cd experiments/turkish-stt
uv venv -p 3.12 .venv
uv pip install -p .venv/bin/python soundfile soxr jiwer num2words numpy sounddevice
# (yalnız mihu) Model (~70 MB int8 + 261 MB fp32 encoder) -> models/turkish-stt (gitignore'lu)
GIT_LFS_SKIP_SMUDGE=1 git clone https://huggingface.co/mihuai/turkish-stt models/turkish-stt
for f in encoder.int8.onnx encoder.onnx decoder.onnx joiner.int8.onnx; do
  curl -L -o models/turkish-stt/$f https://huggingface.co/mihuai/turkish-stt/resolve/main/$f; done
for i in $(seq -f "%03g" 0 80); do curl -sL -o models/turkish-stt/eval/tts-synthetic/audio/c_$i.wav \
  https://huggingface.co/mihuai/turkish-stt/resolve/main/eval/tts-synthetic/audio/c_$i.wav; done
```

> **Önemli:** Model kartının önerdiği `sherpa-onnx==1.13.4`, macOS arm64 tekerleğinde bu modelle
> boş/çöp çıktı veriyor (kendi `eval/run_eval.py`'leri bile %100 WER). `1.13.8` ile kendi sentetik
> setlerinde ilan edilen sonuç tekrarlanıyor (%15,20 WER / %3,55 CER; ilan: 15,37 / 3,57).

## Çalıştırma

```bash
./run.sh          # tüm setler, sıralı; rapor -> runs/summary.md
./run.sh user     # yalnız kullanıcı kayıtları (aşağıya bakın)
```

Parçalar: `prepare.py` (setleri `runs/sets/*.jsonl` olarak kurar; kaynak sesler yerinden okunur),
`eval.py` (bir model/ayar × set), `score.py` (WER/CER, tablolar, örnekler).

## Setler

| set | içerik | not |
|---|---|---|
| `human` | `candan-lite/voice/{arda,ayhan,havva,melis,neva}` — 5 kişi × (12 read + 6 natural), MacBook mikrofonu | **ana set**; referans = okunan cümle (`session.json`); natural klipler referanssız, yalnız örnek transkript |
| `say-tr` | `prompts.json` 24 asistan komutu, `say -v Yelda` | sentetik; özel isim/marka/sayı/tarih kategorili |
| `mihu-synth` | mihuai `eval/tts-synthetic` (81 klip, 24 kHz -> 16 kHz) | modelin **kendi** seti, yanlı olabilir |
| `say-en` | `prompts.json` 5 İngilizce cümle, `say -v Samantha` | mihu'nun İngilizce davranışı |
| `user` | `audio/user/*.wav` + `refs.json` | kullanıcının kendi kaydı (varsa) |

## Kendi sesinizle ölçüm

```bash
.venv/bin/python record.py      # 24 TR + 5 EN cümle, tek tek; Enter başlat/bitir, r tekrar, p dinle
./run.sh user                   # user setini ölç; runs/summary.md'de ayrı satır
```

İlk çalıştırmada macOS mikrofon izni sorabilir. Varsayılan giriş aygıtı kullanılır. Yarıda bırakılırsa
kaldığı yerden devam eder; tek cümle yeniden: `record.py --redo 05`. Elle de eklenebilir: `audio/user/NN.wav`
(16 kHz mono PCM16) + `audio/user/refs.json` (`{"clips":[{"id","wav","text","lang","kategori"}]}`).

## Ölçüm yöntemi

- **mihu:** `eval/run_eval.py` ile aynı yapılandırma (int8 encoder, `modified_beam_search`, 0,5 sn sessiz kuyruk +
  `input_finished`). Ses 100 ms parçalarla verilir; gerçek zamanlı akış simüle edilir: *ilk ara sonuç* = ilk boş
  olmayan kısmi sonucun hazır olduğu an (ses başından), *son sonuç gecikmesi* = ses bittikten (bas-konuş
  bırakıldıktan) sonra final metne kadar geçen süre. Tepe RSS = süreç `ru_maxrss`. 1/4 thread, int8 ve fp32.
- **whisper:** `src/speech.ts`'teki çağrı birebir: `-m … -l auto -t 8 -nt -np -f x.wav` (config: `language: auto`,
  `whisper_threads: 8`, Metal açık). Ek: `-l tr` (8 ve 4 thread); kısa sette 1 thread ve `-ng` CPU referansı. Her dosya
  ayrı süreç (asistan da öyle çalışır), yani dosya başı süre model yüklemeyi içerir. RSS `/usr/bin/time -l`.
  `guardText` (halüsinasyon filtresi) uygulanmaz.
- **whisper --server** (`whisper-server` adı): asistanın yeni yolu (`src/whisper.ts`). Sunucu bir kez başlar,
  dosyalar HTTP ile sıcak modele gönderilir; aynı çözme ayarı (`-bo 5 -bs 5` = CLI varsayılanı). Ayrıca soğuk
  (başlatma -> ilk metin) süresi ve sunucu `ps rss` tepe değeri kaydedilir.
- **Normalizasyon:** Türkçe küçük harf (İ/ı doğru), kesme işareti silinir, noktalama atılır. *Ham*: rakamlar olduğu
  gibi; *sayı*: rakam -> Türkçe yazı (`num2words`), ardışık sayı kelimeleri birleşik.

## Lisans (mihu Community License 1.0) — özet

Yıllık geliri 500 bin USD altındaki kişi/kuruluşa ücretsiz kullanım, çoğaltma, türev (kişisel, araştırma ve —
mihu'nun istediği kayıt yapıldıktan sonra — ticari). Üstünde ayrı ticari sözleşme gerekir. Ürün/arayüz/dokümanda
**"Powered by mihu"** ve NOTICE dosyası zorunlu; çıktılarla rakip konuşma/temel model eğitmek ve modeli yeniden
dağıtmak/satmak yasak.
