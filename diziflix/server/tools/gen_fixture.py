#!/usr/bin/env python3
"""Deterministic catalogue fixture generator for diziflix.

Produces ~120 wholly invented titles (no real show/film names) with genres,
years, ratings, invented overviews and, for series, 1-3 seasons x 6-10 episodes.

    python tools/gen_fixture.py [--out data/fixture.json] [--count 120]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys

SEED = 42

GENRES = [
    ("Dram", "drama"),
    ("Gerilim", "thriller"),
    ("Komedi", "comedy"),
    ("Bilim Kurgu", "scifi"),
    ("Suç", "crime"),
    ("Belgesel", "documentary"),
    ("Aksiyon", "action"),
    ("Romantik", "romance"),
    ("Animasyon", "animation"),
    ("Korku", "horror"),
]

ADJ = [
    "Kayıp", "Sessiz", "Kırık", "Son", "Gizli", "Karanlık", "Uzak", "Yalnız",
    "Soğuk", "Çelik", "Kızıl", "Beyaz", "Derin", "Yedinci", "Ölü", "Sonsuz",
    "Kuzey", "Yanık", "Sahte", "Ters", "Boş", "Ağır", "Keskin", "Solgun",
    "Mavi", "Vahşi", "Sabırsız", "Görünmez", "Eğri", "Yorgun",
]

NOUN = [
    "Sinyal", "Kule", "Vadi", "Gece", "Kapı", "Yörünge", "Mevsim", "İz",
    "Perde", "Sahil", "Fener", "Yankı", "Duvar", "Kuyu", "Basamak", "Rüzgâr",
    "Defter", "Ada", "Tren", "Anahtar", "Bahçe", "Ayna", "Köprü", "Dosya",
    "Yemin", "Kafes", "Harita", "Saat", "Kum", "Orman", "Mektup", "Yol",
    "Kanat", "Sürgün", "Ateş", "Baraj", "Yıldız", "Damar", "Zemin", "Kanal",
]

SUFFIX = [
    "", "", "", "", "", "", "",
    ": Dönüş", ": İlk Bölüm", " Protokolü", " Hattı", " Vakası",
    " Günleri", " Sonrası", " Dosyası", " Hikâyesi",
]

OV_A = [
    "Küçük bir kasabada başlayan sıradan bir gün",
    "Yıllar sonra memleketine dönen bir kadın",
    "Kapatılmak üzere olan bir kurumun son ekibi",
    "Bir gecede her şeyini kaybeden genç bir mühendis",
    "Terk edilmiş bir istasyonda çalışan iki teknisyen",
    "Ailesinin sırlarını araştıran bir arşivci",
    "Emekliliğine haftalar kalan bir müfettiş",
    "Şehrin en eski mahallesinde büyüyen üç arkadaş",
    "Kayıtlara geçmemiş bir kazadan sağ kurtulan tek kişi",
    "Yeni bir kimlikle hayata başlayan bir öğretmen",
    "Uzun bir sessizlikten sonra yeniden açılan bir dosya",
    "Kimsenin inanmadığı bir tanıklığın peşine düşen gazeteci",
]

OV_B = [
    "beklenmedik bir keşifle altüst olur",
    "yavaş yavaş çözülen bir yalanın ortasında kalır",
    "geçmişin bıraktığı izleri takip etmek zorunda kalır",
    "kendi verdiği bir karara mahkûm olur",
    "çevresindeki herkesin farklı bir hikâye anlattığını fark eder",
    "kaybettiğini sandığı şeyin hâlâ orada olduğunu görür",
    "kurallarını kendi koyduğu bir oyunun içine sürüklenir",
    "susmanın da bir tür itiraf olduğunu öğrenir",
    "elindeki tek kanıtı korumak için her şeyi göze alır",
    "bir gecede iki hayat arasında seçim yapmak zorundadır",
]

OV_C = [
    "Anlatı, sessizliğin en gürültülü hâlini arıyor.",
    "Hikâye ilerledikçe kimin haklı olduğu bulanıklaşıyor.",
    "Her bölüm, verilen bir sözün bedelini biraz daha büyütüyor.",
    "Tempolu kurgusuyla soluksuz bir izlek sunuyor.",
    "Sıcak bir mizahla ağır bir meseleyi dengeliyor.",
    "Görsel dili, karakterlerin söyleyemediklerini üstleniyor.",
    "Küçük detaylar, finalde bambaşka bir anlam kazanıyor.",
    "Gerçekle kurgu arasındaki çizgiyi bilerek belirsiz bırakıyor.",
]

EP_TITLES = [
    "Yankı", "Kırılma", "İlk Işık", "Sağanak", "Kayıt Dışı", "Ters Akıntı",
    "Soğuk Başlangıç", "Boşluk", "Uzun Gece", "Yarım Kalan", "Ağır Su",
    "Son Çağrı", "Kapalı Devre", "Toz", "Kuzeye Doğru", "Sessiz Alarm",
    "Dönüş Yolu", "İkinci Perde", "Kırık Cam", "Gecikmeli", "Kör Nokta",
    "Ara Verme", "Sığınak", "Düğüm", "Çözülme", "Fazla Mesai", "Bahar Söküğü",
    "Ateş Hattı", "Yeraltı", "Kapanış",
]

FIRST = [
    "Deniz", "Kerem", "Aylin", "Sinan", "Nehir", "Barış", "Elif", "Tuna",
    "Şule", "Emre", "Yaren", "Kaan", "Melis", "Ozan", "Ceren", "Arda",
    "İpek", "Volkan", "Simge", "Doruk", "Nazlı", "Berk", "Ela", "Cem",
]

LAST = [
    "Aydın", "Erdoğdu", "Kansu", "Toprak", "Yalçın", "Demirsoy", "Akgün",
    "Bozkurt", "Ergin", "Sarıkaya", "Uçar", "Tanrıöver", "Özsoy", "Kavas",
    "Güneş", "Berkin", "Solmaz", "Yıldıran", "Öncel", "Kaplan",
]

BADGES = [None, None, None, None, "YENİ", "POPÜLER", "ÖZEL"]


def person(rnd: random.Random) -> str:
    return f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"


def make_title(rnd: random.Random, used: set) -> str:
    for _ in range(500):
        t = f"{rnd.choice(ADJ)} {rnd.choice(NOUN)}{rnd.choice(SUFFIX)}"
        if t not in used:
            used.add(t)
            return t
    t = f"{rnd.choice(ADJ)} {rnd.choice(NOUN)} {len(used)}"
    used.add(t)
    return t


def make_overview(rnd: random.Random) -> str:
    return f"{rnd.choice(OV_A)}, {rnd.choice(OV_B)}. {rnd.choice(OV_C)}"


def generate(count: int = 120) -> dict:
    rnd = random.Random(SEED)
    used_titles: set = set()
    items = []
    s_no = 0
    m_no = 0

    for i in range(count):
        is_series = rnd.random() < 0.62
        if is_series:
            s_no += 1
            item_id = f"s_{s_no:04d}"
        else:
            m_no += 1
            item_id = f"m_{m_no:04d}"

        title = make_title(rnd, used_titles)
        primary = rnd.choice(GENRES)[0]
        extra = [g[0] for g in GENRES if g[0] != primary]
        rnd.shuffle(extra)
        genres = [primary] + extra[: rnd.choice([0, 1, 1, 2])]
        year = rnd.randint(2009, 2025)
        rating = round(rnd.uniform(5.6, 9.4), 1)
        popularity = round(rnd.uniform(0, 100), 2)

        item = {
            "id": item_id,
            "type": "series" if is_series else "movie",
            "title": title,
            "year": year,
            "overview": make_overview(rnd),
            "genres": genres,
            "rating": rating,
            "badge": rnd.choice(BADGES),
            "popularity": popularity,
            "added_at": rnd.randint(1, 900),  # days ago, lower = newer
            "tagline": rnd.choice(OV_C),
            "director": person(rnd),
            "cast": [person(rnd) for _ in range(rnd.randint(3, 6))],
            "seasons": [],
        }

        if is_series:
            item["runtime"] = rnd.choice([38, 42, 45, 48, 52])
            for s in range(1, rnd.randint(1, 3) + 1):
                episodes = []
                for e in range(1, rnd.randint(6, 10) + 1):
                    episodes.append(
                        {
                            "id": f"{item_id}_s{s:02d}e{e:02d}",
                            "season": s,
                            "episode": e,
                            "title": rnd.choice(EP_TITLES),
                            "overview": make_overview(rnd),
                            "runtime": item["runtime"] + rnd.choice([-4, -2, 0, 2, 5]),
                        }
                    )
                item["seasons"].append(
                    {"season": s, "title": f"{s}. Sezon", "episodes": episodes}
                )
        else:
            item["runtime"] = rnd.randint(88, 154)

        items.append(item)

    return {
        "version": 1,
        "seed": SEED,
        "genres": [{"name": n, "slug": s} for n, s in GENRES],
        "items": items,
    }


def main() -> int:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description="Generate diziflix catalogue fixture")
    ap.add_argument("--out", default=os.path.join(here, "data", "fixture.json"))
    ap.add_argument("--count", type=int, default=120)
    args = ap.parse_args()

    data = generate(args.count)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    eps = sum(len(s["episodes"]) for i in data["items"] for s in i["seasons"])
    print(f"wrote {args.out}: {len(data['items'])} items, {eps} episodes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
