#!/usr/bin/env python3
"""Bir yapılandırmayı bir sette çalıştırır -> runs/results/<config>/<set>.jsonl + <set>.meta.json

  mihu:    python eval.py mihu --set human --threads 1 [--fp32] [--name mihu-int8-t1]
  whisper: python eval.py whisper --set human --lang auto --threads 8 [--cpu] [--name whisper-asistan]
  sunucu:  python eval.py whisper --set human --lang auto --threads 8 --server [--name whisper-server]

mihu: sherpa-onnx OnlineRecognizer (eval/run_eval.py ile aynı: transducer, modified_beam_search).
Ses 100 ms parçalarla akıtılır; gerçek zamanlı akış simüle edilir (parça k, t=(k+1)*0.1 s'de gelir,
işleme seri): ilk ara sonuç zamanı (ses başından) ve son sonuç gecikmesi (ses bitişinden sonra) ölçülür.
Tepe RSS = tüm set boyunca süreç ru_maxrss.

whisper: asistanın src/speech.ts çağrısıyla birebir aynı argümanlar (-m -l -t -nt -np -f), her dosya ayrı
süreç (asistan da öyle yapar; yani dosya başı süre model yüklemeyi içerir). RSS /usr/bin/time -l ile.
"""
import argparse, json, os, re, resource, subprocess, sys, time, wave
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ASSISTANT = os.path.abspath(os.path.join(HERE, "../.."))
MODEL = os.path.join(HERE, "models/turkish-stt")
CHUNK = 1600  # 100 ms @ 16 kHz


def load(p):
    with wave.open(p) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768


def run_mihu(a, rows):
    import sherpa_onnx
    t0 = time.perf_counter()
    rec = sherpa_onnx.OnlineRecognizer.from_transducer(
        tokens=f"{MODEL}/tokens.txt", encoder=f"{MODEL}/encoder{'' if a.fp32 else '.int8'}.onnx",
        decoder=f"{MODEL}/decoder.onnx", joiner=f"{MODEL}/joiner.int8.onnx",
        num_threads=a.threads, decoding_method="modified_beam_search", provider="cpu")
    load_ms = (time.perf_counter() - t0) * 1000
    # ısınma (ilk çağrıdaki ORT kurulum maliyeti ayrıca raporlanır)
    t0 = time.perf_counter(); s = rec.create_stream(); s.accept_waveform(16000, np.zeros(16000, np.float32)); s.input_finished()
    while rec.is_ready(s): rec.decode_stream(s)
    warm_ms = (time.perf_counter() - t0) * 1000
    out = []
    for r in rows:
        x = load(r["wav"]); dur = len(x) / 16000
        s = rec.create_stream(); clock = 0.0; first = None; compute = 0.0
        for k in range(0, len(x), CHUNK):
            arrive = min(k + CHUNK, len(x)) / 16000
            c0 = time.perf_counter(); s.accept_waveform(16000, x[k:k + CHUNK])
            while rec.is_ready(s): rec.decode_stream(s)
            dt = time.perf_counter() - c0; compute += dt
            clock = max(clock, arrive) + dt
            if first is None and rec.get_result(s).strip(): first = clock
        # bas-konuş bırakıldı: 0.5 s sessiz kuyruk + input_finished (run_eval.py ile aynı)
        c0 = time.perf_counter(); s.accept_waveform(16000, np.zeros(8000, np.float32)); s.input_finished()
        while rec.is_ready(s): rec.decode_stream(s)
        hyp = rec.get_result(s); dt = time.perf_counter() - c0; compute += dt
        clock = max(clock, dur) + dt
        out.append({**r, "hyp": hyp if isinstance(hyp, str) else hyp.text, "ms": round(compute * 1000, 1),
                    "first_partial_s": None if first is None else round(first, 3),
                    "final_latency_ms": round((clock - dur) * 1000, 1)})
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20  # macOS: bayt
    return out, {"load_ms": round(load_ms), "warmup_ms": round(warm_ms), "peak_rss_mb": round(rss)}


def run_whisper(a, rows):
    bin_ = os.path.join(ASSISTANT, "runtime/whisper/whisper-cli")
    model = os.path.join(ASSISTANT, "models/ggml-large-v3-q5_0.bin")
    base = [bin_, "-m", model, "-l", a.lang, "-t", str(a.threads), "-nt"] + (["-ng"] if a.cpu else [])
    # soğuk yükleme dökümü: bir kez -np'siz çalıştır, whisper'ın kendi zaman çizelgesini oku
    p = subprocess.run(base + ["-f", rows[0]["wav"]], capture_output=True, text=True, cwd=ASSISTANT)
    m = re.search(r"load time =\s*([\d.]+) ms", p.stderr)
    out, peak = [], 0
    for r in rows:
        t0 = time.perf_counter()
        p = subprocess.run(["/usr/bin/time", "-l"] + base + ["-np", "-f", r["wav"]], capture_output=True, text=True, cwd=ASSISTANT)
        ms = (time.perf_counter() - t0) * 1000
        rss = int(re.search(r"(\d+)\s+maximum resident set size", p.stderr).group(1)) / 2**20
        peak = max(peak, rss)
        hyp = re.sub(r"\s+", " ", p.stdout).strip()
        out.append({**r, "hyp": hyp, "ms": round(ms, 1), "first_partial_s": None, "final_latency_ms": round(ms, 1),
                    "rc": p.returncode})
    return out, {"load_ms": float(m.group(1)) if m else None, "peak_rss_mb": round(peak),
                 "args": " ".join(base[3:]) + " -np -f <wav>"}


def run_whisper_server(a, rows):
    import signal, socket, urllib.request, uuid
    bin_ = os.path.join(ASSISTANT, "runtime/whisper/whisper-server")
    model = os.path.join(ASSISTANT, "models/ggml-large-v3-q5_0.bin")
    with socket.socket() as s: s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    base = ["-m", model, "-l", a.lang, "-t", str(a.threads), "-nt", "-bo", "5", "-bs", "5"] + (["-ng"] if a.cpu else [])
    url = f"http://127.0.0.1:{port}"

    def post(path):
        b = uuid.uuid4().hex; data = open(path, "rb").read()
        body = (f"--{b}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\ntext\r\n"
                f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"speech.wav\"\r\n"
                f"Content-Type: audio/wav\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
        req = urllib.request.Request(url + "/inference", body, {"Content-Type": f"multipart/form-data; boundary={b}"})
        with urllib.request.urlopen(req, timeout=300) as r: return r.read().decode()

    def rss(pid):
        return int(subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip() or 0) / 1024

    t0 = time.perf_counter()
    proc = subprocess.Popen([bin_] + base + ["--host", "127.0.0.1", "--port", str(port), "--public", "/nonexistent"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ASSISTANT)
    try:
        while True:
            try:
                with urllib.request.urlopen(url + "/health", timeout=2) as r:
                    if r.status == 200: break
            except Exception: pass
            if proc.poll() is not None: sys.exit("whisper-server başlatılamadı")
            time.sleep(0.05)
        load_ms = (time.perf_counter() - t0) * 1000
        post(rows[0]["wav"]); cold_ms = (time.perf_counter() - t0) * 1000
        out, peak = [], rss(proc.pid)
        for r in rows:
            t1 = time.perf_counter(); hyp = post(r["wav"]); ms = (time.perf_counter() - t1) * 1000
            peak = max(peak, rss(proc.pid))
            out.append({**r, "hyp": re.sub(r"\s+", " ", hyp).strip(), "ms": round(ms, 1), "first_partial_s": None,
                        "final_latency_ms": round(ms, 1), "rc": 0})
    finally:
        proc.send_signal(signal.SIGTERM); proc.wait(10)
    return out, {"load_ms": round(load_ms), "cold_ms": round(cold_ms), "peak_rss_mb": round(peak),
                 "args": " ".join(base[2:]) + " (whisper-server, sıcak)"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("engine", choices=["mihu", "whisper"])
    ap.add_argument("--set", required=True)
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--fp32", action="store_true")
    ap.add_argument("--lang", default="auto")
    ap.add_argument("--cpu", action="store_true", help="whisper: -ng (Metal kapalı)")
    ap.add_argument("--server", action="store_true", help="whisper: kalıcı whisper-server (asistanın yeni yolu)")
    ap.add_argument("--name")
    a = ap.parse_args()
    name = a.name or (f"mihu-{'fp32' if a.fp32 else 'int8'}-t{a.threads}" if a.engine == "mihu"
                      else f"whisper{'-server' if a.server else ''}-{a.lang}-t{a.threads}{'-cpu' if a.cpu else ''}")
    if a.engine == "mihu" and not os.path.exists(os.path.join(MODEL, "tokens.txt")):
        print(f"mihu atlandı: {MODEL} yok (model silindi; yeniden indirme: README.md > Kurulum)"); return
    rows = [json.loads(l) for l in open(os.path.join(HERE, "runs/sets", a.set + ".jsonl"), encoding="utf-8")]
    out, meta = (run_mihu if a.engine == "mihu" else run_whisper_server if a.server else run_whisper)(a, rows)
    d = os.path.join(HERE, "runs/results", name); os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, a.set + ".jsonl"), "w", encoding="utf-8") as f:
        for r in out: f.write(json.dumps(r, ensure_ascii=False) + "\n")
    meta.update(config=name, set=a.set, clips=len(out), audio_s=round(sum(r["dur"] for r in out), 1),
                compute_s=round(sum(r["ms"] for r in out) / 1000, 2))
    json.dump(meta, open(os.path.join(d, a.set + ".meta.json"), "w"), indent=1)
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
