"""The provider library (K4, README §5.9): a video host's URL + Referer in, a ``ProviderResult`` out. Knows no site."""
from .registry import catalog, providers, resolve  # noqa: F401
