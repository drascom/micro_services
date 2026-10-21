"""Import this FIRST in every test module (before any ``app`` import).

- Points DATA_DIR (and with it the db, image cache, pages, providers) at a throw-away directory and blanks the TMDB key,
  whatever ``.env`` says.
- ``DIZIFLIX_FAKE_LLM=1``: the LLM caller (T06) must use its fake in tests; no real pi/LLM call.
- Network off: ``socket.connect`` to anything but loopback / unix sockets raises ``SandboxNetworkError``; proxy env vars
  point at a dead local port so C-level clients that honour them (libcurl) cannot leave the machine either.
- Tripwire: at exit the real ``server2/data`` is compared with how it looked at import time; any difference prints
  ``SANDBOX VIOLATION`` and exits with status 3. If another process (a local ``uvicorn`` on :8091) is writing the real
  data dir, the comparison is skipped with one stderr line (``SANDBOX_SKIP_LINE``); the tests still run in the temp dir.
"""
import atexit
import ipaddress
import os
import shutil
import socket
import subprocess
import sys
import time
import tempfile

_SERVER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SERVER not in sys.path:
    sys.path.insert(0, _SERVER)

REAL_DATA = os.path.realpath(os.path.join(_SERVER, "data"))


def _fingerprint():
    out = {}
    for base, dirs, files in os.walk(REAL_DATA):
        for name in files:
            path = os.path.join(base, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            out[path] = None if os.sep + "imgcache" + os.sep in path else (st.st_size, st.st_mtime_ns)
    return out


SANDBOX_SKIP_LINE = ("SANDBOX: yerel sunucu data/ dizinine yazıyor; "
                     "gerçek veri kontrolü atlandı (testler yine geçici dizinde)")
LOCAL_SERVER_PORT = 8091
WAL_FRESH_S = 120


def _port_listening(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def outside_writer(data_dir: str = REAL_DATA) -> bool:
    """True when some other process (a local server) holds/writes the real data dir.

    lsof on the db file (other PIDs than ours) or a listener on 127.0.0.1:8091; if lsof is missing or fails,
    a recently modified ``diziflix2.db-wal`` also counts."""
    db = os.path.join(data_dir, "diziflix2.db")
    lsof_ok = False
    if os.path.exists(db):
        try:
            res = subprocess.run(["lsof", "-t", db], capture_output=True, text=True, timeout=5)
            # rc 1 + empty output = nobody has it open (a valid answer)
            if res.returncode in (0, 1):
                lsof_ok = True
                pids = {int(x) for x in res.stdout.split() if x.strip().isdigit()}
                if pids - {os.getpid()}:
                    return True
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    if _port_listening(LOCAL_SERVER_PORT):
        return True
    if not lsof_ok:
        try:
            if time.time() - os.stat(db + "-wal").st_mtime < WAL_FRESH_S:
                return True
        except OSError:
            pass
    return False


class SandboxNetworkError(ConnectionRefusedError):
    """A test tried to reach a non-loopback address."""


def _is_local(address) -> bool:
    if not isinstance(address, tuple) or not address:
        return True  # AF_UNIX path / abstract socket
    host = address[0]
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    host = str(host).split("%", 1)[0]
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # a hostname: would need DNS + a real peer


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


def _guarded_connect(self, address):
    if not _is_local(address):
        raise SandboxNetworkError(f"sandbox: network is off in tests (connect to {address!r})")
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    if not _is_local(address):
        raise SandboxNetworkError(f"sandbox: network is off in tests (connect to {address!r})")
    return _real_connect_ex(self, address)


socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex

ROOT = os.environ.get("DIZIFLIX_TEST_SANDBOX")
if not ROOT:
    _before = _fingerprint()
    _skip_tripwire = outside_writer()
    if _skip_tripwire:
        sys.stderr.write(SANDBOX_SKIP_LINE + "\n")
        sys.stderr.flush()

    def _tripwire():
        if _skip_tripwire:
            return
        after = _fingerprint()
        if after != _before:
            changed = sorted(p for p in set(_before) | set(after) if _before.get(p) != after.get(p))
            sys.stderr.write("SANDBOX VIOLATION: the tests changed the real data dir: %s\n"
                             % ", ".join(os.path.relpath(p, REAL_DATA) for p in changed[:10]))
            sys.stderr.flush()
            os._exit(3)

    atexit.register(_tripwire)  # registered before the rmtree below, so it runs after the cleanup
    ROOT = tempfile.mkdtemp(prefix="diziflix2-test-")
    atexit.register(shutil.rmtree, ROOT, ignore_errors=True)
    dead_proxy = "http://127.0.0.1:9"
    os.environ.update({
        "DIZIFLIX_TEST_SANDBOX": ROOT,
        "DATA_DIR": ROOT,
        # "" (not unset): .env is loaded with setdefault, so an empty value keeps the real key out
        "TMDB_ACCESS_KEY": "",
        "STREAM_PROXY_SECRET": "test-secret",
        "PI_BIN": "",
        "OBSCURA_BIN": "",
        "DIZIFLIX_FAKE_LLM": "1",
        "HTTP_PROXY": dead_proxy, "HTTPS_PROXY": dead_proxy, "ALL_PROXY": dead_proxy,
        "http_proxy": dead_proxy, "https_proxy": dead_proxy, "all_proxy": dead_proxy,
        "NO_PROXY": "localhost,127.0.0.1,::1", "no_proxy": "localhost,127.0.0.1,::1",
    })

_cfg = sys.modules.get("app.config")
if _cfg is not None and os.path.realpath(_cfg.DATA_DIR) != os.path.realpath(ROOT):
    raise RuntimeError("app.config was imported before tests/_sandbox.py: the tests would use the real data dir")
