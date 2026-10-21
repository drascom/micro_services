"""Genre id -> Turkish label (``/api/catalog`` / ``/api/genres``; copied from the old ``genres.GENRES``)."""

GENRES = dict(zip(
    "action comedy drama horror science_fiction thriller romance animation documentary adventure crime fantasy family war "
    "history music western mystery biography sport youth children".split(),
    "Aksiyon|Komedi|Dram|Korku|Bilim Kurgu|Gerilim|Romantik|Animasyon|Belgesel|Macera|Suç|Fantastik|Aile|Savaş|Tarih|"
    "Müzikal|Western|Gizem|Biyografi|Spor|Gençlik|Çocuk".split("|")))
