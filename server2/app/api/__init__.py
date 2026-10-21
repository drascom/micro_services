"""Client API (API.md, T04b). ``ROUTERS``: every client router module, in registration order (``main.py`` adds them)."""
from . import detail, health, home, images, notify, profiles, progress, search, stream_proxy, streams, subtitles

ROUTERS = [profiles, home, detail, streams, progress, notify, search, subtitles, stream_proxy, images]
