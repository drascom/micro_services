import _sandbox  # noqa: F401  (must be first)

import types
import unittest

from app.control.routing import home_targets
from app.control.sitemap import labels
from app.control.stages import a1_home, a2_listing, a3_title, hints
from app.core.extract import blocks as x_blocks, episodes as x_episodes

BASE = "https://fakesite-t31.test"
URL = {"url": {"selector": "a", "attr": "href"}}


def _cards(cls: str, head: str, n: int, tag: str = "li") -> str:
    return "".join(f'<{tag} class="{cls}"><a href="{head}/{head}-{i}"><img data-src="/p/{head}{i}.jpg" alt="{head} {i}">'
                   f'<h2>{head.title()} {i}</h2></a></{tag}>' for i in range(1, n + 1))


def film_page() -> str:
    """A film archive: a slider, the paged "son eklenen" grid, a "dikkate değer" strip (three row shapes)."""
    return (f'<html><head><base href="{BASE}/"></head><body><main>'
            f'<div class="slider"><h2 class="title">Popüler Filmler</h2><div class="track">{_cards("slide", "film", 6, "div")}</div></div>'
            f'<div class="latest"><h2 class="title">Son Eklenen Filmler</h2><ul class="grid">{_cards("movie-li", "film", 10)}</ul>'
            '<div class="pagination"><a href="filmler/2" rel="next">&gt;</a></div></div>'
            f'<div class="notable"><h2 class="title">Dikkate Değer Filmler</h2><ul class="strip">{_cards("poster-sm", "film", 5)}</ul></div>'
            '</main></body></html>')


class MultiBlockTests(unittest.TestCase):
    def test_film_archive_gets_own_rows_blocks_and_block_pagination(self):
        html = film_page()
        ctx = types.SimpleNamespace()
        own = a2_listing.own_rule(ctx, BASE + "/filmler", html, {"row_selector": "td.poster", "fields": URL})
        self.assertEqual(own["row_selector"], "li.movie-li")   # the paged block is the list's own row rule
        specs = a2_listing.page_specs(ctx, BASE + "/filmler", html)
        found = x_blocks.find_multi(html, specs, BASE + "/filmler")
        self.assertEqual([b["heading"] for b in found], ["Popüler Filmler", "Son Eklenen Filmler", "Dikkate Değer Filmler"])
        self.assertEqual([bool(b.get("pagination")) for b in found], [False, True, False])
        entries = [a2_listing._entry("/filmler", b, "movie", True) for b in found]
        entries[0] = labels.apply_block(entries[0], "ignore")
        rows = x_blocks.parse_rows(html, "td.poster", URL, entries)
        self.assertEqual(sorted({r["_block"] for r in rows}), [2, 3])
        self.assertEqual(len(rows), 15)
        listing = {"row_selector": "td.poster", "fields": URL, "blocks": entries,
                   "listings": [{"url": "/filmler", "title_type": "movie", **own}]}
        self.assertEqual(labels.rule_for(listing, BASE + "/filmler/")["row_selector"], "li.movie-li")
        self.assertEqual(labels.rule_for(listing, BASE + "/diziler")["row_selector"], "td.poster")
        rule, walked = labels.pagination_for(listing, BASE + "/filmler")
        self.assertEqual((rule["kind"], walked), ("next_link", {2}))


class LabelTests(unittest.TestCase):
    def test_home_target_prefers_existing_rows_and_names_new_ones(self):
        cases = {"trend": "role:trending", "Trendler": "role:trending", "diziler": "role:latest_series",
                 "Tüm Diziler": "role:latest_series", "filmler": "role:latest_movies",
                 "Dikkate Değer Filmler": "role:noteworthy_movies", "slider": "slider", "yok": "yok",
                 "diziler özel": "new:Diziler Özel", "ilginç seçki": "new:İlginç Seçki"}
        for text, want in cases.items():
            self.assertEqual(home_targets.propose(text), want, text)

    def test_section_target_follows_card_addresses(self):
        titles = [BASE + f"/dizi/show-{i}-izle-3" for i in range(5)]
        episodes = [BASE + f"/dizi/show-{i}/sezon-1/bolum-{i + 1}" for i in range(5)]
        self.assertEqual(a1_home.card_target(titles), "title")
        self.assertEqual(a1_home.card_target(episodes), "episode")
        self.assertIsNone(a1_home.card_target([BASE + "/koleksiyon/a", BASE + "/koleksiyon/b"]))

    def test_unpunctuated_note_takes_the_nearest_meaning_word(self):
        note = ("trend bölümlerine gidecek olmazsa olmaz diziler özel bölümüne gidecek tüm filmler için /filmler-hd linkini "
                "kullan tüm diziler için /diziler-hd linkini kullan")
        got = {l["url"]: l["meaning"] for l in hints.note_items(note)["listings"]}
        self.assertEqual(got, {"/filmler-hd": "film_list", "/diziler-hd": "series_list"})


class OwnershipAndSampleTests(unittest.TestCase):
    def test_season_paths_are_not_foreign_with_relative_links(self):
        rows = "".join(f'<div class="ep"><a href="dizi/kara-kus/sezon-{s}/bolum-{e}">{e}. Bölüm</a></div>'
                       for s in (1, 2) for e in (1, 2, 3))
        html = (f'<html><head><base href="{BASE}/"></head><body><div class="eps">{rows}</div>'
                '<a href="dizi/kara-kus/sezon-2">Sezon 2</a></body></html>')
        spec = {"row_selector": "div.eps div.ep", "fields": URL, "default_season": 1,
                "episode_url_regex": r"^/?dizi/([^/]+)/sezon-(?P<season>\d+)/bolum-(?P<episode>\d+)/?$"}
        inv = x_episodes.series_inventory(html, BASE + "/dizi/kara-kus", spec)
        self.assertEqual((inv["ownership"]["rows"], inv["ownership"]["foreign"]), (6, 0))

    def test_a3_samples_hold_a_film_and_a_series(self):
        series = [BASE + f"/izle/s{i}" for i in range(6)]
        films = [BASE + "/izle/f1"]
        tps = {**{u: "series" for u in series}, **{u: "movie" for u in films}}
        got = a3_title._both_types([], series[:5], series + films, tps)[:a3_title.MAX_SAMPLES]
        self.assertIn(films[0], got)
        self.assertEqual(sum(1 for u in got if tps[u] == "series"), 4)


if __name__ == "__main__":
    unittest.main()
