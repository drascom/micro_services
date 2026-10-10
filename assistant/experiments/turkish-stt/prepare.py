#!/usr/bin/env python3
"""Test setlerini runs/sets/<set>.jsonl olarak hazırlar. Kaynak sesler yerinden okunur;
dönüşüm gerekiyorsa (24 kHz -> 16 kHz, say -> wav) yalnız runs/audio16k/ altına yazılır.

Setler:
  human      candan-lite/voice/* (session.json'lu, completed). read=referanslı, natural=referanssız
  mihu-synth mihuai eval/tts-synthetic (modelin kendi seti, yanlı olabilir)
  say-tr     prompts.json TR cümleleri, macOS `say -v Yelda` (sentetik)
  say-en     prompts.json EN cümleleri, macOS `say -v Samantha` (sentetik)
  user       audio/user/refs.json (record.py ile kullanıcı kaydı; varsa)
"""
import csv, json, glob, os, subprocess, wave

HERE = os.path.dirname(os.path.abspath(__file__))
VOICE = os.environ.get("VOICE_DIR", "/Users/drascom/Documents/work/candan-lite/voice")
MIHU = os.path.join(HERE, "models/turkish-stt/eval/tts-synthetic")
OUT = os.path.join(HERE, "runs/sets")
A16 = os.path.join(HERE, "runs/audio16k")


def dur(p):
    with wave.open(p) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, p
        return w.getnframes() / 16000


def to16k(src, dst):
    if not os.path.exists(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", src, "-ar", "16000", "-ac", "1",
                        "-c:a", "pcm_s16le", dst], check=True)
    return dst


def write(name, rows):
    os.makedirs(OUT, exist_ok=True)
    for r in rows:
        r["set"] = name; r["dur"] = round(dur(r["wav"]), 3)
    with open(os.path.join(OUT, name + ".jsonl"), "w", encoding="utf-8") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{name}: {len(rows)} klip, {sum(r['dur'] for r in rows):.0f} sn")


def human():
    rows = []
    for s in sorted(glob.glob(os.path.join(VOICE, "*/*/session.json"))):
        d = json.load(open(s, encoding="utf-8"))
        if not d.get("completed") or "eksik" in s: continue
        base = os.path.dirname(s)
        for c in d["clips"]:
            rows.append({"id": f"{d['speaker_slug']}-{c['prompt_id']}", "wav": os.path.join(base, c["path"]),
                         "ref": c["prompt"] if c["kind"] == "read" else None, "lang": "tr",
                         "speaker": d["speaker_slug"], "kind": c["kind"], "kategori": c["prompt_id"]})
    write("human", rows)


def mihu():
    rows = []
    if not os.path.exists(os.path.join(MIHU, "manifest.tsv")):
        if not os.path.exists(os.path.join(HERE, "runs/sets/mihu-synth.jsonl")):
            print("mihu-synth atlandı: models/turkish-stt silindi (yeniden indirme: README.md > Kurulum)")
        return
    with open(os.path.join(MIHU, "manifest.tsv"), encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            src = os.path.join(MIHU, r["audio"])
            rows.append({"id": os.path.basename(src)[:-4], "wav": to16k(src, os.path.join(A16, "mihu", os.path.basename(src))),
                         "ref": r["reference"], "lang": "tr", "speaker": r["voice"], "kind": "synth", "kategori": "mihu"})
    write("mihu-synth", rows)


def say():
    P = json.load(open(os.path.join(HERE, "prompts.json"), encoding="utf-8"))["prompts"]
    for lang, voice, name in [("tr", "Yelda", "say-tr"), ("en", "Samantha", "say-en")]:
        rows = []
        for p in [p for p in P if p["lang"] == lang]:
            aiff = os.path.join(A16, name, p["id"] + ".aiff"); wav = aiff[:-5] + ".wav"
            if not os.path.exists(wav):
                os.makedirs(os.path.dirname(aiff), exist_ok=True)
                subprocess.run(["say", "-v", voice, "-o", aiff, p["text"]], check=True)
                to16k(aiff, wav); os.remove(aiff)
            rows.append({"id": p["id"], "wav": wav, "ref": p["text"], "lang": lang, "speaker": voice,
                         "kind": "synth", "kategori": p["kategori"]})
        write(name, rows)


def user():
    refs = os.path.join(HERE, "audio/user/refs.json")
    if not os.path.exists(refs): print("user: audio/user/refs.json yok, atlandı"); return
    rows = []
    for r in json.load(open(refs, encoding="utf-8"))["clips"]:
        wav = os.path.join(os.path.dirname(refs), r["wav"])
        if os.path.exists(wav):
            rows.append({"id": r["id"], "wav": wav, "ref": r["text"], "lang": r.get("lang", "tr"),
                         "speaker": "user", "kind": "read", "kategori": r.get("kategori", "")})
    write("user", rows)


if __name__ == "__main__":
    human(); mihu(); say(); user()
