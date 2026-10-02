"""Import this FIRST in every test module (before any ``app`` import).

Points every path the server writes to (db, image cache, ops settings, TMDB previews, scraper state) at a
throw-away directory and blanks the TMDB credentials, so no test can read or modify the real ``server/data``
or reach the real TMDB, whatever ``.env`` says. Tests that need a key/DB still patch their own values on top.

Tripwire: when the process exits, the real ``server/data`` is compared with how it looked at import time
(file sizes + mtimes, image-cache listing); any difference prints ``SANDBOX VIOLATION`` and exits with status 3,
so a test that leaks into the real data fails the whole run (a live server writing there at the same time
would trip it too - run the suite when no server uses that directory).
"""
import atexit
import os
import shutil
import sys
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
            # image cache: existence is enough (a download would add a file); everything else: size + mtime
            out[path] = None if os.sep + "imgcache" + os.sep in path else (st.st_size, st.st_mtime_ns)
    return out


ROOT = os.environ.get("DIZIFLIX_TEST_SANDBOX")
if not ROOT:
    _before = _fingerprint()

    def _tripwire():
        after = _fingerprint()
        if after != _before:
            changed = sorted(p for p in set(_before) | set(after) if _before.get(p) != after.get(p))
            sys.stderr.write("SANDBOX VIOLATION: the tests changed the real data dir: %s\n"
                             % ", ".join(os.path.relpath(p, REAL_DATA) for p in changed[:10]))
            sys.stderr.flush()
            os._exit(3)

    atexit.register(_tripwire)  # registered before the rmtree below, so it runs after the cleanup
    ROOT = tempfile.mkdtemp(prefix="diziflix-test-")
    atexit.register(shutil.rmtree, ROOT, ignore_errors=True)
    os.environ.update({
        "DIZIFLIX_TEST_SANDBOX": ROOT,
        "DATA_DIR": ROOT,
        "DB_PATH": os.path.join(ROOT, "diziflix.db"),
        "IMG_CACHE_DIR": os.path.join(ROOT, "imgcache"),
        # "" (not unset): .env is loaded with setdefault, so an empty value keeps the real key out
        "TMDB_ACCESS_KEY": "", "TMDB_TOKEN": "", "TMDB_API_KEY": "",
        # no test may reach YouTube (library/trailer_check.py); tests of the check enable it and mock the probe
        "TRAILER_CHECK": "0",
        # a play request without a stream starts the source finder (library/sourcefinder.py: search + repair agent): off by
        # default in every test; tests of the finder switch it on and fake its steps
        "SOURCEFINDER_ENABLED": "0",
        # a failed playback report probes the source's streams in the background (library/streamdiag.py): off in every test (no
        # test may reach a real host from a thread); tests of the diagnosis patch config.STREAM_DIAG and fake the HTTP client
        "STREAM_DIAG": "0",
        "STREAM_PROBE_RANK": "0",
        # the hardening criteria of a NEW site's onboarding (onboard_sandbox.harden_enabled: availability_gate_defined, series_signal_collection,
        # series_full_inventory, home_path_is_canonical) are off by default so the older onboarding suites keep exercising their own subject;
        # tests/test_onboard_harden.py switches them on
        "ONBOARD_HARDEN": "0",
    })

_cfg = sys.modules.get("app.config")
if _cfg is not None and os.path.realpath(_cfg.DATA_DIR) != os.path.realpath(ROOT):
    raise RuntimeError("app.config was imported before tests/_sandbox.py: the tests would use the real data dir")
