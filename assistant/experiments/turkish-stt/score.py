#!/usr/bin/env python3
"""runs/results/* -> runs/summary.md (tablolar + örnekler). Kullanım: python score.py

Normalizasyon (hem referans hem hipotez):
  ham : Türkçe küçük harf (İ->i, I->ı), kesme işareti silinir (İstanbul'da -> istanbulda),
        diğer noktalama boşluk olur. Rakamlar olduğu gibi kalır ("9" != "dokuz").
  sayı: ham + rakamlar Türkçe yazıya ("9" -> "dokuz", "%18" -> "yüzde onsekiz") ve ardışık sayı
        kelimeleri birleşik yazılır ("kırk iki" -> "kırkiki"), mihu run_eval.py'deki rakam<->kelime
        birleştirmesiyle aynı amaç.
"""
import glob, json, os, re, statistics as st
import jiwer
from num2words import num2words

HERE = os.path.dirname(os.path.abspath(__file__))
NUMW = "sıfır bir iki üç dört beş altı yedi sekiz dokuz on yirmi otuz kırk elli altmış yetmiş seksen doksan yüz bin milyon".split()
NUMRE = re.compile("^(" + "|".join(sorted(NUMW, key=len, reverse=True)) + ")+$")


def norm(t, num):
    t = (t or "").replace("İ", "i").replace("I", "ı").lower()
    if num:
        t = re.sub(r"%\s*(\d)", r"yüzde \1", t)
        t = re.sub(r"(\d)[.\s](\d{3})\b", r"\1\2", t)  # 1.250 / 1 250 -> 1250
        t = re.sub(r"\d+", lambda m: " " + num2words(int(m.group()), lang="tr") + " ", t)
    t = re.sub(r"['’`]", "", t)
    t = re.sub(r"[^\w\s]|_", " ", t)
    w = t.split()
    if num:
        out = []
        for x in w:
            if out and NUMRE.match(x) and NUMRE.match(out[-1]): out[-1] += x
            else: out.append(x)
        w = out
    return " ".join(w)


def wer(rows, num):
    R = [norm(r["ref"], num) for r in rows]; H = [norm(r["hyp"], num) for r in rows]
    keep = [i for i, r in enumerate(R) if r]
    if not keep: return None, None
    R = [R[i] for i in keep]; H = [H[i] or "∅" for i in keep]
    return jiwer.wer(R, H) * 100, jiwer.cer(R, H) * 100


def pct(xs, q):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def load():
    res = {}
    for f in sorted(glob.glob(os.path.join(HERE, "runs/results/*/*.jsonl"))):
        cfg = os.path.basename(os.path.dirname(f)); s = os.path.basename(f)[:-6]
        rows = [json.loads(l) for l in open(f, encoding="utf-8")]
        meta = json.load(open(f[:-6] + ".meta.json"))
        res[(cfg, s)] = (rows, meta)
    return res


def fmt(x, d=1): return "–" if x is None else f"{x:.{d}f}"


def main():
    res = load(); L = []
    order = ["human", "user", "say-tr", "mihu-synth", "say-en"]
    L += ["## Ana tablo", "",
          "WER/CER yalnız referanslı klipler (human: read). WER ham = rakam normalize edilmeden; WER/CER sayı = rakam->yazı.",
          "ms/dosya = medyan (p95). mihu: saf hesap süresi (model sıcak); whisper: asistandaki gibi süreç başlatma + model yükleme dahil.",
          "Akış: ilk ara sonuç (ses başından, medyan s) / son sonuç gecikmesi (ses bitiminden sonra, medyan ms).", "",
          "| model/ayar | set | n | WER ham | WER sayı | CER sayı | RTF | ms/dosya | tepe RSS MB | soğuk yükleme ms | akış: ilk ara / son |",
          "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for s in order:
        for (cfg, s2), (rows, meta) in sorted(res.items()):
            if s2 != s: continue
            ref = [r for r in rows if r["ref"]]
            w0, _ = wer(ref, False); w1, c1 = wer(ref, True)
            ms = [r["ms"] for r in rows]
            fp = [r["first_partial_s"] for r in rows if r.get("first_partial_s") is not None]
            stream = f"{st.median(fp):.2f} s / {st.median([r['final_latency_ms'] for r in rows]):.0f} ms" if cfg.startswith("mihu") else "– (toplu)"
            L.append(f"| {cfg} | {s} | {len(ref)} | {fmt(w0)} | {fmt(w1)} | {fmt(c1)} | {meta['compute_s']/meta['audio_s']:.3f} | "
                     f"{st.median(ms):.0f} ({pct(ms, .95):.0f}) | {meta['peak_rss_mb']} | {fmt(meta.get('load_ms'), 0)} | {stream} |")
    # konuşmacı bazında (human read)
    cfgs = sorted({c for c, s in res if s == "human"})
    if cfgs:
        spk = sorted({r["speaker"] for r in res[(cfgs[0], "human")][0]})
        L += ["", "## human (read) — konuşmacı bazında WER sayı / CER sayı", "",
              "| model/ayar | " + " | ".join(spk) + " |", "|---|" + "---:|" * len(spk)]
        for c in cfgs:
            rows = res[(c, "human")][0]; cells = []
            for p in spk:
                w, cc = wer([r for r in rows if r["speaker"] == p and r["ref"]], True); cells.append(f"{w:.1f} / {cc:.1f}")
            L.append(f"| {c} | " + " | ".join(cells) + " |")
        # natural: referans yok, örnek transkript
        L += ["", "## human (natural) — referanssız, örnek transkriptler (ilk 160 karakter)", ""]
        nat = [r for r in res[(cfgs[0], "human")][0] if r["kind"] == "natural"]
        pick = []
        for p in spk: pick += [r["id"] for r in nat if r["speaker"] == p][:1]
        for i in pick:
            L.append(f"**{i}**")
            for c in cfgs:
                r = next(r for r in res[(c, "human")][0] if r["id"] == i); L.append(f"- {c}: {r['hyp'][:160]}")
            L.append("")
    # kategori bazlı hata örnekleri
    cats = ("ozel-isim", "marka", "sayi", "tarih", "saat", "read-03-numbers", "read-04-address", "read-01-turkish-chars")
    L += ["## Kategori bazlı hata örnekleri (say-tr + human read; sayı-normalize WER>0)", ""]
    for c in sorted({c for c, _ in res}):
        ex = []
        for s in ("say-tr", "human", "user"):
            if (c, s) not in res: continue
            for r in res[(c, s)][0]:
                if r["ref"] and any(r["kategori"].startswith(k) for k in cats) and wer([r], True)[0] > 0: ex.append(r)
        seen, out = set(), []
        for r in ex:  # kategori çeşitliliği
            k = r["kategori"]
            if k not in seen: seen.add(k); out.append(r)
        L.append(f"**{c}**")
        for r in out[:6]: L.append(f"- [{r['set']}/{r['kategori']}] ref: {r['ref']}  \n  hyp: {r['hyp']}")
        L.append("")
    # İngilizce
    L += ["## İngilizce (say-en, Samantha)", ""]
    for c in sorted({c for c, s in res if s == "say-en"}):
        L.append(f"**{c}**")
        for r in res[(c, "say-en")][0]: L.append(f"- {r['ref']} → `{r['hyp'] or '∅ (boş)'}`")
        L.append("")
    open(os.path.join(HERE, "runs/summary.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
