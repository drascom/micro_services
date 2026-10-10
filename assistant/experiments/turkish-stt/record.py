#!/usr/bin/env python3
"""Kullanıcının kendi sesiyle test seti kaydı -> audio/user/NN.wav (16 kHz mono PCM16) + audio/user/refs.json

  .venv/bin/python record.py              # prompts.json'daki tüm cümleler (kayıtlı olanlar atlanır)
  .venv/bin/python record.py --redo 05    # yalnız 05'i yeniden kaydet
  .venv/bin/python record.py --dry-run    # mikrofon açmadan akış/yazma testi (runs/record-dryrun/)

NOT: İlk çalıştırmada macOS "Terminal mikrofona erişmek istiyor" izni sorabilir; izin verin
(Sistem Ayarları > Gizlilik ve Güvenlik > Mikrofon). Sistemdeki VARSAYILAN giriş aygıtı kullanılır.

Her cümlede: Enter = kayda başla, Enter = bitir; sonra Enter = kaydet ve geç, r = tekrar kaydet,
p = dinle, s = atla, q = çık. Kayıt ölçümü: ./run.sh user
"""
import argparse, json, os, subprocess, sys, tempfile, wave
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
SR = 16000


def save_wav(path, pcm):
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(pcm.astype(np.int16).tobytes())


def write_refs(out, prompts, done):
    clips = [{"id": p["id"], "wav": p["id"] + ".wav", "text": p["text"], "lang": p["lang"], "kategori": p["kategori"]}
             for p in prompts if p["id"] in done]
    json.dump({"note": "record.py ile kaydedildi; referans = ekranda gösterilen cümle", "sample_rate": SR, "clips": clips},
              open(os.path.join(out, "refs.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def record(sd):
    """Enter'a kadar kaydet. Önce 16 kHz dener; aygıt desteklemezse aygıt hızında kaydedip soxr ile indirger."""
    chunks = []
    try:
        rate = SR; stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", callback=lambda d, *_: chunks.append(d.copy()))
    except Exception:
        rate = int(sd.query_devices(kind="input")["default_samplerate"])
        stream = sd.InputStream(samplerate=rate, channels=1, dtype="float32", callback=lambda d, *_: chunks.append(d.copy()))
    with stream:
        input("  ● KAYIT — bitirince Enter ")
    x = np.concatenate(chunks)[:, 0] if chunks else np.zeros(0, np.float32)
    if rate != SR:
        import soxr; x = soxr.resample(x, rate, SR)
    return (np.clip(x, -1, 1) * 32767).astype(np.int16)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--redo", nargs="*", default=[], help="yeniden kaydedilecek id'ler (ör. 05 12)")
    a = ap.parse_args()
    prompts = json.load(open(os.path.join(HERE, "prompts.json"), encoding="utf-8"))["prompts"]
    out = os.path.join(HERE, "runs/record-dryrun" if a.dry_run else "audio/user"); os.makedirs(out, exist_ok=True)
    import sounddevice as sd
    dev = sd.query_devices(kind="input")
    print(f"Giriş aygıtı (varsayılan): {dev['name']} ({int(dev['default_samplerate'])} Hz)  |  {len(prompts)} cümle -> {out}")
    if a.dry_run:  # mikrofon açılmaz: her cümle için 1 sn'lik 440 Hz ton yazılır, refs.json üretilir, okunup doğrulanır
        t = np.arange(SR) / SR
        for p in prompts: save_wav(os.path.join(out, p["id"] + ".wav"), 0.2 * 32767 * np.sin(2 * np.pi * 440 * t))
        write_refs(out, prompts, {p["id"] for p in prompts})
        r = json.load(open(os.path.join(out, "refs.json"), encoding="utf-8"))
        with wave.open(os.path.join(out, r["clips"][0]["wav"])) as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (SR, 1, 2)
        print(f"dry-run OK: {len(r['clips'])} klip, refs.json geçerli"); return
    done = {p["id"] for p in prompts if os.path.exists(os.path.join(out, p["id"] + ".wav"))} - set(a.redo)
    todo = [p for p in prompts if p["id"] not in done and (not a.redo or p["id"] in a.redo)]
    print("Mikrofondan ~20-30 cm, normal konuşma hızınızla, ekrandaki cümleyi aynen okuyun.\n")
    for i, p in enumerate(todo, 1):
        while True:
            print(f"[{i}/{len(todo)}] {p['id']} ({p['lang']}, {p['kategori']})\n    » {p['text']}")
            cmd = input("  Enter = kayda başla, s = atla, q = çık: ").strip().lower()
            if cmd == "q": write_refs(out, prompts, done); print("Çıkıldı; kaydedilenler korundu."); return
            if cmd == "s": break
            pcm = record(sd); dur = len(pcm) / SR; peak = np.abs(pcm).max(initial=0) / 32767
            warn = " (ÇOK SESSİZ?)" if peak < 0.03 else " (KIRPILMA?)" if peak > 0.99 else ""
            print(f"  {dur:.1f} sn, tepe {peak:.2f}{warn}")
            while True:
                c = input("  Enter = kaydet, r = tekrar, p = dinle: ").strip().lower()
                if c == "p":
                    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
                        save_wav(f.name, pcm); subprocess.run(["afplay", f.name])
                    continue
                break
            if c == "r": continue
            save_wav(os.path.join(out, p["id"] + ".wav"), pcm); done.add(p["id"]); write_refs(out, prompts, done)
            print("  kaydedildi\n"); break
    write_refs(out, prompts, done)
    print(f"Bitti: {len(done)} kayıt, {out}/refs.json. Ölçüm için: ./run.sh user")


if __name__ == "__main__":
    main()
