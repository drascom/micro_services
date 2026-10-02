"""Faz 2c-1: the pi onboarding skill (server/pi/skills/diziflix-site-onboarding) and extension (server/pi/extensions).

* the generated blocks of the skill references match the code (``tools/gen_onboard_refs.py``, like docs/api-samples);
* SKILL.md frontmatter, the files it points at, the tool names it uses;
* every yaml example in the references is valid against the real validators (resolvers / normalize / sandbox checks / provider
  recipes), and every provider recipe of ``references/examples/providers/`` finds its stream in a sample player page;
* the extension is loaded by ``node`` (type stripping) with a fake ``pi`` object and talks to a fake sandbox: tool set,
  endpoints, header, body keys against the sandbox request models, error texts, output shrinking, timeout, abort;
  its ``tool_call`` guard lets pi's built-in ``read`` open the skill directory only (``..`` / symlink / relative / prefix
  escapes, no ``DIZIFLIX_SKILL_DIR``).
No real pi, no real network (a fake HTTP server on 127.0.0.1)."""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _sandbox  # noqa: F401,E402  throw-away DATA_DIR/DB + no TMDB key; must precede any `app` import
import base64
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import yaml

from app.library import normalize as nrm
from app.routers import onboard_sandbox as sb
from app.scraper import heal_agent, onboard, pi_agent, resolvers
from app.scraper.fetch import FetchError
from app.scraper.providers import recipes, registry
from app.scraper.resolvers import _unpack
from tools import gen_onboard_refs as gen

PI = Path(__file__).resolve().parent.parent / "pi"
SKILL_DIR = PI / "skills" / "diziflix-site-onboarding"
REFS = SKILL_DIR / "references"
EXTENSION = PI / "extensions" / "diziflix-onboard.ts"
TOOLS = ["discover_site", "fetch_page", "query_html", "grep_page", "outline_page", "test_config", "list_resolvers", "test_resolvers",
         "test_provider", "match_providers", "test_search", "ask_user", "submit_draft"]
#: edit mode: the onboarding tools without ``discover_site`` (the site exists) + the read-only ``load_site_config``
EDIT = [name for name in TOOLS if name != "discover_site"]
#: tools the extension answers itself (no sandbox call): ``ask_user`` ends the run ``needs_input``, the server reads the question from pi's event
LOCAL_TOOLS = ("ask_user",)
HTTP_TOOLS = [name for name in TOOLS if name not in LOCAL_TOOLS]
#: tool -> (method, sandbox path, request model of the endpoint)
ENDPOINTS = {
    "fetch_page": ("POST", "/fetch", sb.FetchBody),
    "query_html": ("POST", "/query", sb.QueryBody),
    "grep_page": ("POST", "/grep", sb.GrepBody),
    "outline_page": ("POST", "/outline", sb.OutlineBody),
    "test_config": ("POST", "/test_config", sb.TestConfigBody),
    "list_resolvers": ("GET", "/resolvers", None),
    "test_resolvers": ("POST", "/test_resolvers", sb.TestResolversBody),
    "test_provider": ("POST", "/test_provider", sb.TestProviderBody),
    "match_providers": ("POST", "/match_providers", sb.MatchProvidersBody),
    "discover_site": ("POST", "/discover_site", sb.DiscoverSiteBody),
    "test_search": ("POST", "/test_search", sb.TestSearchBody),
    "submit_draft": ("POST", "/submit", sb.SubmitBody),
}
PREFIX = "/api/onboard/sandbox"
FENCE = re.compile(r"```yaml\n(.*?)```", re.S)
RECIPE_DIR = REFS / "examples" / "providers"


def example_recipes():
    """file stem -> the parsed recipe of ``references/examples/providers/<stem>.yaml``."""
    return {p.stem: yaml.safe_load(p.read_text(encoding="utf-8")) for p in sorted(RECIPE_DIR.glob("*.yaml"))}


def texts():
    """(file name, text) of SKILL.md and every reference markdown."""
    out = [("SKILL.md", (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8"))]
    out += [(p.name, p.read_text(encoding="utf-8")) for p in sorted(REFS.glob("*.md"))]
    return out


class GeneratedReferences(unittest.TestCase):
    def test_generated_blocks_are_current(self):
        self.assertEqual(gen.outdated(), [], "run: venv/bin/python -m tools.gen_onboard_refs")

    def test_stale_block_is_detected(self):
        text = (REFS / "resolvers.md").read_text(encoding="utf-8")
        self.assertEqual(gen.render_file("resolvers.md"), text)
        tampered = text.replace("| `selector` |", "| `selektor` |", 1)
        self.assertNotEqual(tampered, text)
        self.assertEqual(gen.replace_block(tampered, "resolvers-catalog", gen.render_resolvers()), text)

    def test_missing_markers_are_an_error(self):
        with self.assertRaises(ValueError):
            gen.replace_block("no markers here", "resolvers-catalog", "x")

    def test_catalog_names_are_in_the_reference(self):
        text = (REFS / "resolvers.md").read_text(encoding="utf-8")
        for entry in resolvers.catalog():
            self.assertIn("### `%s`" % entry["type"], text)
            for name in entry["params"]:
                self.assertIn("`%s`" % name, text)
        for entry in registry.catalog():
            if entry["kind"] == "code":   # the recipes of the library change at run time: list_resolvers shows them
                self.assertIn("`%s`" % entry["name"], text)
                self.assertIn("| `%s` | code |" % entry["name"], text)
        self.assertIn("`list_resolvers`", text.split("### Providers", 1)[1])

    def test_quality_reference_uses_the_sandbox_constants(self):
        text = (REFS / "quality.md").read_text(encoding="utf-8")
        self.assertIn(">= %d" % sb.MIN_VALID_COUNT, text)
        for name, minimum in sb.MIN_FILL.items():
            self.assertIn("| `%s_fill` | >= %g |" % (name, minimum), text)
        self.assertIn("| `collections_valid_count` | >= %d |" % sb.MIN_COLLECTION_COUNT, text)
        self.assertIn("| `collections_normalize_ok_ratio` | >= %g |" % sb.MIN_NORMALIZE_OK_RATIO, text)
        self.assertIn("| `playable_ratio` | >= %g |" % sb.MIN_PLAYABLE_RATIO, text)
        self.assertIn("| `series_have_episode_sources` | >= %g |" % sb.MIN_SERIES_SOURCE_RATIO, text)
        self.assertIn("up to %d DIFFERENT normalized titles" % sb.PLAYABLE_SAMPLES, text)
        self.assertIn("| `search_ok` | >= 1 |", text)
        for name in ("playable_ratio", "series_have_episode_sources", "search_ok"):   # the "what fixes what" table names every new criterion
            self.assertIn("| `%s` | `" % name, text.split("## What fixes what")[1], name)

    def test_collection_roles_come_from_the_shared_vocabulary(self):
        from app.scraper import collections as col
        text = (REFS / "collections.md").read_text(encoding="utf-8")
        for role, feeds in col.ROLES.items():
            self.assertIn("| `%s` |" % role, text)
        for role in sb.ONBOARD_ROLES:
            self.assertIn(role, col.ROLES)
        self.assertTrue(set(col.HOME_ROLES) <= set(sb.ONBOARD_ROLES))
        tampered = text.replace("| `trending` |", "| `trendy` |", 1)
        self.assertNotEqual(tampered, text)
        self.assertEqual(gen.replace_block(tampered, "collection-roles", gen.render_collection_roles()), text)


class SkillFiles(unittest.TestCase):
    def test_frontmatter(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        meta = yaml.safe_load(text.split("---\n", 2)[1])
        self.assertEqual(meta["name"], SKILL_DIR.name)
        self.assertEqual(meta["name"], "diziflix-site-onboarding")
        self.assertTrue(re.fullmatch(r"[a-z0-9-]{1,64}", meta["name"]))
        self.assertTrue(0 < len(meta["description"]) <= 1024)
        self.assertEqual(set(meta), {"name", "description"})

    def test_mentions_every_tool_and_existing_references(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for name in TOOLS:
            self.assertIn("`%s(" % name if name != "list_resolvers" else "`list_resolvers()", text, name)
        paths = set(re.findall(r"`(references/[\w./-]+)`", text))
        self.assertTrue({"references/config-schema.md", "references/normalize.md", "references/resolvers.md",
                         "references/quality.md"} <= paths)
        for rel in paths:
            self.assertTrue((SKILL_DIR / rel).exists(), rel)   # a file, or a directory of example files (references/examples/providers/)

    def test_references_are_read_with_the_read_tool_inside_the_skill_dir(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("`read(path)`", text)
        self.assertIn("ABSOLUTE path", text)
        self.assertIn("ONLY inside that skill directory", text)

    def test_examples_are_real_configs(self):
        for name in ("sinemalar.yaml", "yabancidizi.yaml"):
            data = yaml.safe_load((REFS / "examples" / name).read_text(encoding="utf-8"))
            self.assertTrue(data["base_url"].startswith("https://"), name)
            self.assertEqual(sb._check_fields(data), [], name)
            self.assertEqual(sb._check_core(data)[0], [], name)

    def test_tool_names_are_the_extension_tool_names(self):
        source = EXTENSION.read_text(encoding="utf-8")
        # the thirteen onboarding tools (twelve sandbox calls + the local ask_user), then the two repair-mode tools (registered only when DIZIFLIX_MODE=repair)
        self.assertEqual(re.findall(r'^    name: "(\w+)",$', source, re.M), TOOLS + ["load_site_config", "submit_repair"])


class YamlExamples(unittest.TestCase):
    """Every ```yaml block of the skill is checked with the validators the sandbox itself uses."""

    def blocks(self):
        for name, text in texts():
            for index, body in enumerate(FENCE.findall(text)):
                data = yaml.safe_load(body)
                self.assertIsInstance(data, dict, "%s block %d is not a mapping" % (name, index))
                yield name, index, data

    def test_blocks_validate(self):
        seen = {"full": 0, "normalize": 0, "resolvers": 0, "providers": 0, "recipes": 0}
        recipe_names = set(example_recipes())   # `providers: [referer_player]` names an example recipe of the library
        for name, index, data in self.blocks():
            where = "%s block %d" % (name, index)
            if "normalize" in data:
                seen["normalize"] += 1
                self.assertEqual(nrm.validate_rules(data["normalize"]), [], where)
            if "resolvers" in data:
                seen["resolvers"] += 1
                self.assertEqual(resolvers.validate(data["resolvers"]), [], where)
            if "providers" in data:
                seen["providers"] += 1
                known = {p.name for p in registry.PROVIDERS} | recipe_names
                self.assertTrue(set(data["providers"]) <= known, where)
            if "match" in data and "extract" in data:   # a provider recipe
                seen["recipes"] += 1
                self.assertEqual(recipes.validate_recipe(data), [], where)
            if "list" in data and "base_url" in data:
                seen["full"] += 1
                errors, _warnings = sb._check_core(data)
                play_errors, play_warnings = sb._check_playback(data)
                self.assertEqual(errors + sb._check_fields(data) + play_errors + sb._check_normalize(data), [], where)
                self.assertEqual(play_warnings, [], where)
        self.assertGreaterEqual(seen["full"], 1)
        self.assertGreaterEqual(seen["normalize"], 3)   # two worked examples + the full example
        self.assertGreaterEqual(seen["resolvers"], 5)
        self.assertGreaterEqual(seen["providers"], 3)
        self.assertGreaterEqual(seen["recipes"], 1)   # the format example of providers.md

    def test_normalize_examples_do_what_the_text_says(self):
        raw = {"title": "Dizi", "detail_url": "/dizi/some-show/sezon-4/bolum-2", "season": None, "episode": None}
        for name, _index, data in self.blocks():
            rules = data.get("normalize")
            if name != "normalize.md" or not rules or not rules.get("host"):
                continue
            out = nrm.generic_normalize(rules, raw, base_url="https://yabancidizi.news")
            self.assertEqual(out["source_key"], "dizi/some-show")
            self.assertEqual(out["type"], "series")
            self.assertEqual(out["video_sources"][0]["key"], "s4e2")
            return
        self.fail("no host-checking normalize example found")

    def test_playable_chain_examples_build_episodes(self):
        """The "Playable chain" section of normalize.md: episode pages as cards keep the SHOW as key and carry the episode."""
        text = (REFS / "normalize.md").read_text(encoding="utf-8")
        section = text.split("## Playable chain")[1]
        rules = [yaml.safe_load(b)["normalize"] for b in FENCE.findall(section)]
        self.assertEqual(len(rules), 2)
        episode_pages, no_season = rules
        got = nrm.generic_normalize(episode_pages, {"title": "T", "detail_url": "/some-show-2-sezon-5-bolum-izle-full-tek-parca/"},
                                    base_url="https://example.com")
        self.assertEqual((got["source_key"], got["type"]), ("some-show", "series"))
        self.assertEqual([(v["key"], v["url"], v["resolver"]) for v in got["video_sources"]],
                         [("s2e5", "https://example.com/some-show-2-sezon-5-bolum-izle-full-tek-parca/", "page")])
        self.assertIsNone(nrm.generic_normalize(episode_pages, {"title": "T", "detail_url": "/diziler/some-show-izle/"},
                                                base_url="https://example.com"))   # a series PAGE is not an episode page
        got = nrm.generic_normalize(no_season, {"title": "T", "detail_url": "/some-show-7-bolum-izle/"}, base_url="https://example.com")
        self.assertEqual([(v["key"], v["season"], v["episode"]) for v in got["video_sources"]], [("s1e7", 1, 7)])
        out = nrm.preview(episode_pages, [{"title": "A", "detail_url": "/a-1-sezon-1-bolum-x/"}], base_url="https://example.com")
        self.assertEqual((out["episode_items"], out["with_video_sources"], out["series_without_sources"]), (1, 1, 0))


class EditModeSkill(unittest.TestCase):
    """EDIT mode (``onboard.start(mode="edit")``): the skill's "Edit mode" section, ``references/edit.md`` and its generated blocks."""

    def skill(self):
        return (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    def test_the_skill_sends_an_edit_message_to_the_edit_reference(self):
        text = self.skill()
        meta = yaml.safe_load(text.split("---\n", 2)[1])
        self.assertIn("EDIT MODE", meta["description"])
        self.assertLessEqual(len(meta["description"]), 1024)
        section = text.split("## Edit mode", 1)[1].split("\n## ", 1)[0]
        for needle in ("EDIT mode for site <site_id>", "references/edit.md", "load_site_config(site_id)", "NARROWEST",
                       "never a rewrite", "test_config(playable: true, collections: true)",
                       "submit_draft(yaml_text, site_id_suggestion = <site_id>", "NEW VERSION"):
            self.assertIn(needle, section, needle)
        self.assertLess(text.index("## Edit mode"), text.index("## Task"))   # before the new-site workflow: it is skipped in edit mode
        self.assertTrue((REFS / "edit.md").is_file())

    def test_the_server_message_is_the_one_the_skill_expects(self):
        message = onboard.edit_first_message("demo", "voeg trendler toe")
        first = message.split("\n", 1)[0]
        self.assertEqual(first, "/skill:diziflix-site-onboarding EDIT mode for site demo. User request: voeg trendler toe")
        self.assertIn(first.split(" ", 1)[1].replace("demo", "<site_id>").split(". User")[0], self.skill())   # "EDIT mode for site <site_id>"
        for needle in ("references/edit.md", 'load_site_config("demo")', "test_config(playable: true, collections: true)",
                       'site_id_suggestion = "demo"'):
            self.assertIn(needle, message, needle)
        self.assertIn("(none given", onboard.edit_first_message("demo", "  "))

    def test_the_edit_reference_states_the_rules(self):
        text = (REFS / "edit.md").read_text(encoding="utf-8")
        for needle in ("EDIT mode for site <site_id>", "ONLY the site being edited", "Narrowest change", "Never touch identity",
                       "No field dropping", "Do not break what works", "needs code:", "submit_draft", "NEW VERSION", "provider_recipes",
                       "references/providers.md", "references/collections.md", "references/search.md", "ONE clear question"):
            self.assertIn(needle, text, needle)
        for rel in set(re.findall(r"`(references/[\w./-]+)`", text)):
            self.assertTrue((SKILL_DIR / rel).exists(), rel)

    def test_the_edit_blocks_are_generated_from_the_code(self):
        self.assertEqual(gen.TARGETS["edit.md"], ("edit-tools", "edit-layers"))
        text = (REFS / "edit.md").read_text(encoding="utf-8")
        self.assertEqual(gen.render_file("edit.md"), text)
        tools = text.split("BEGIN GENERATED edit-tools", 1)[1].split("END GENERATED edit-tools", 1)[0]
        for name in pi_agent.EDIT_TOOLS:
            self.assertIn("`%s`" % name, tools)
        self.assertEqual(list(pi_agent.EDIT_TOOLS), EDIT + ["load_site_config"])
        self.assertNotIn("discover_site", pi_agent.EDIT_TOOLS)   # the site exists: nothing to discover
        self.assertNotIn("submit_repair", tools)
        layers = text.split("BEGIN GENERATED edit-layers", 1)[1].split("END GENERATED edit-layers", 1)[0]
        for name, (keys, _doc) in heal_agent.LAYERS.items():   # the same layers (and keys) as a repair's scope gate
            row = [line for line in layers.splitlines() if line.startswith("| `%s` |" % name)]
            self.assertEqual(len(row), 1, name)
            for key in keys:
                self.assertIn("`%s`" % key, row[0], name)
        for name in gen.EDIT_EXTRA_LAYERS:
            self.assertIn("| `%s` |" % name, layers)
        for key in ("search", "list_url", "item_limit", "image_hosts"):
            self.assertIn("`%s`" % key, layers)

    def test_a_stale_edit_block_is_detected_and_a_missing_layer_doc_is_an_error(self):
        text = (REFS / "edit.md").read_text(encoding="utf-8")
        tampered = text.replace("| `fetch_mode` |", "| `fetch_modes` |", 1)
        self.assertNotEqual(tampered, text)
        self.assertEqual(gen.replace_block(tampered, "edit-layers", gen.render_edit_layers()), text)
        with patch.dict(gen.EDIT_LAYER_DOCS, clear=False):
            del gen.EDIT_LAYER_DOCS["fetch"]
            with self.assertRaises(ValueError):
                gen.render_edit_layers()
        with patch.dict(gen.EDIT_EXTRA_LAYERS, {"list": (("list",), "clash")}):
            with self.assertRaises(ValueError):
                gen.render_edit_layers()

    def test_the_edit_tool_set_matches_the_extension_and_the_command(self):
        self.assertEqual(pi_agent.MODES["edit"], pi_agent.EDIT_TOOLS)
        cmd = pi_agent.build_command("od_0123456789ab", tools=pi_agent.EDIT_TOOLS, model_name="p/m")
        self.assertEqual(cmd[cmd.index("--tools") + 1], ",".join((*pi_agent.EDIT_TOOLS, pi_agent.READ_TOOL)))
        source = EXTENSION.read_text(encoding="utf-8")
        self.assertIn('modes: ["onboard", "edit"]', source)
        self.assertIn('modes: ["repair", "edit"]', source)
        self.assertIn('modes: ["repair"]', source)


class PlayableChainSkill(unittest.TestCase):
    """The skill teaches the playable chain: look at ``with_video_sources``, episode pages keep the show as key, say so honestly."""

    def test_skill_rules(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for needle in ("**Playable chain**", "normalize.with_video_sources", "normalize.series_without_sources",
                       "key` is the SHOW slug only", "`episode_source`", "default_season: 1", "needs series inventory",
                       "test_config(yaml_text, page_id, detail_page_id, playable: true, provider_recipes?)", "playable_ratio",
                       "series_have_episode_sources", "`playable` samples resolved"):
            self.assertIn(needle, text, needle)
        self.assertIn("/<show>-<S>-sezon-<E>-bolum-.../", text)
        self.assertLess(text.index("**Playable chain**"), text.index("**Collections (home sections)**"))
        self.assertIn("`test_config(yaml_text, page_id?, detail_page_id?, collections?, playable?, provider_recipes?)`", text)
        reference = (REFS / "normalize.md").read_text(encoding="utf-8")
        for needle in ("## Playable chain", "Episode pages as cards", "default_season", "needs series inventory",
                       "with_video_sources", "series_without_sources"):
            self.assertIn(needle, reference, needle)


class SeriesPageReference(unittest.TestCase):
    """``references/series-page.md`` + the skill's "Series pages" step: the key table comes from ``series_generic.SPEC_KEYS``,
    every example validates with ``series_generic.validate_spec`` and really reads a sample series page."""
    BASE = "https://www.example-dizi.com"

    def reference(self):
        return (REFS / "series-page.md").read_text(encoding="utf-8")

    def specs(self):
        """The ``series_page`` mappings of the reference's yaml blocks, in order."""
        return [yaml.safe_load(b)["series_page"] for b in FENCE.findall(self.reference()) if "series_page" in yaml.safe_load(b)]

    def inventory(self, html, url, spec):
        from app.scraper import site_extractors
        return site_extractors.series_inventory("onboard_draft", html, url, spec)

    def test_the_key_table_is_generated_from_the_engine_keys(self):
        from app.scraper import series_generic
        text = self.reference()
        table = text.split("<!-- BEGIN GENERATED series-page-keys", 1)[1].split("<!-- END GENERATED series-page-keys -->")[0]
        for key in series_generic.SPEC_KEYS:
            self.assertIn("| `%s` |" % key, table, key)
        self.assertEqual(set(gen.SERIES_KEY_DOCS), set(series_generic.SPEC_KEYS))
        self.assertIn("| `row_selector` | yes |", table)
        self.assertIn("| `episode_url_regex` | yes |", table)
        tampered = text.replace("| `default_season` |", "| `default_sezon` |", 1)
        self.assertNotEqual(tampered, text)
        self.assertEqual(gen.replace_block(tampered, "series-page-keys", gen.render_series_page_keys()), text)

    def test_a_new_engine_key_without_a_doc_line_breaks_the_generator(self):
        from app.scraper import series_generic
        with patch.object(series_generic, "SPEC_KEYS", series_generic.SPEC_KEYS | {"brand_new_key"}):
            with self.assertRaises(ValueError) as ctx:
                gen.render_series_page_keys()
        self.assertIn("brand_new_key", str(ctx.exception))

    def test_every_example_validates_and_the_set_is_complete(self):
        from app.scraper import series_generic
        specs = self.specs()
        self.assertEqual(len(specs), 4)
        for index, spec in enumerate(specs):
            self.assertEqual(series_generic.validate_spec(spec), [], "example %d" % (index + 1))
            self.assertTrue(series_generic.is_generic_spec(spec))
        used = set().union(*[set(s) for s in specs])
        self.assertTrue({"row_selector", "episode_url_regex", "fields", "default_season", "series_url_regex", "series_slug_regex",
                         "same_series_regex", "season_pages", "unaired_classes", "first_episode", "last_episode"} <= used)

    def test_the_series_card_normalize_example_builds_a_title_without_episodes(self):
        blocks = [yaml.safe_load(b) for b in FENCE.findall(self.reference())]
        rules = next(b["normalize"] for b in blocks if "normalize" in b)
        self.assertEqual(nrm.validate_rules(rules), [])
        self.assertNotIn("episode_source", rules)
        for path in ("/diziler/alpha-show-izle/", "/diziler/alpha-show/"):
            got = nrm.generic_normalize(rules, {"title": "Alpha Show", "detail_url": path}, base_url=self.BASE)
            self.assertEqual((got["source_key"], got["type"]), ("alpha-show", "series"), path)
            self.assertFalse(got.get("video_sources"), path)   # the card carries no episode: series_page supplies them
            self.assertEqual(got["source_url"], self.BASE + path)
        out = nrm.preview(rules, [{"title": "A", "detail_url": "/diziler/a-izle/"}], base_url=self.BASE)
        self.assertEqual((out["episode_items"], out["series_without_sources"]), (1, 1))   # that is what series_page fills

    def test_example_1_rows_dates_and_similar_series(self):
        html = ('<html><body><ul class="episodes">'
                '<li><a href="/alpha-show-1-sezon-1-bolum-izle-full-tek-parca/"><span class="name">Pilot</span></a><span class="date">24.07.2025</span></li>'
                '<li><a href="/alpha-show-1-sezon-2-bolum-izle-full-tek-parca/"><span class="name">Ikinci</span></a><span class="date">31.07.2025</span></li>'
                '<li><a href="/alpha-show-2-sezon-1-bolum-izle-full-tek-parca/"><span class="name">Donus</span></a><span class="date">no date</span></li>'
                '</ul><div class="similar"><a href="/beta-1-sezon-1-bolum-izle-full-tek-parca/">Beta</a></div></body></html>')
        url = self.BASE + "/diziler/alpha-show-izle/"
        got = self.inventory(html, url, self.specs()[0])
        self.assertEqual(got["warnings"], [])
        self.assertTrue(got["structured"])
        self.assertEqual([(e["season"], e["episode"], e["title"], e.get("air_date")) for e in got["video_sources"]],
                         [(1, 1, "Pilot", "2025-07-24"), (1, 2, "Ikinci", "2025-07-31"), (2, 1, "Donus", None)])
        self.assertEqual(got["video_sources"][0]["url"], self.BASE + "/alpha-show-1-sezon-1-bolum-izle-full-tek-parca/")
        self.assertTrue(all(e["resolver"] == "page" for e in got["video_sources"]))   # the page is resolved at play time
        self.assertEqual(self.inventory(html, self.BASE + "/film/alpha-show/", self.specs()[0])["video_sources"], [])   # not a series page
        # the markup moved (classes renamed): the row selector finds nothing, the link scan still gives the episodes
        moved = self.inventory(html.replace('class="episodes"', 'class="eps"'), url, self.specs()[0])
        self.assertFalse(moved["structured"])
        self.assertEqual([(e["season"], e["episode"]) for e in moved["video_sources"]], [(1, 1), (1, 2), (2, 1)])
        self.assertTrue(any("bağlantı taraması" in w for w in moved["warnings"]), moved["warnings"])

    def test_example_2_no_seasons(self):
        html = ('<html><body><div class="bolumler">' + "".join(f'<a href="/alpha-show-{e}-bolum-izle/">{e}. Bölüm</a>' for e in (1, 2, 3))
                + '</div><a href="/beta-1-bolum-izle/">other</a></body></html>')
        got = self.inventory(html, self.BASE + "/dizi/alpha-show/", self.specs()[1])
        self.assertEqual([(e["season"], e["episode"]) for e in got["video_sources"]], [(1, 1), (1, 2), (1, 3)])
        self.assertEqual(got["declared_seasons"], [])

    def test_example_3_season_pages_unaired_and_first_last(self):
        def row(e, cls="", date="01.02.2025"):
            return (f'<tr class="{cls}"><td class="title"><a href="/dizi/alpha-show/sezon-2/bolum-{e}">Bolum {e}</a></td>'
                    f'<td class="date">{date}</td></tr>')

        html = ('<html><body><div id="seasons">' + "".join(f'<a href="/dizi/alpha-show/sezon-{s}">{s}. Sezon</a>' for s in (1, 2, 3))
                + '</div><a class="first-ep" href="/dizi/alpha-show/sezon-1/bolum-1">Ilk</a>'
                '<a class="last-ep" href="/dizi/alpha-show/sezon-2/bolum-3">Son</a><table class="episodes">'
                + row(1) + row(2) + row(3, "soon", "01.12.2099") + "</table></body></html>")
        got = self.inventory(html, self.BASE + "/dizi/alpha-show/sezon-2", self.specs()[2])
        self.assertEqual([(e["season"], e["episode"], e["title"]) for e in got["video_sources"]], [(2, 1, "Bolum 1"), (2, 2, "Bolum 2")])
        self.assertEqual(got["unaired"], [{"season": 2, "episode": 3, "air_date": "2099-12-01"}])
        self.assertEqual(got["declared_seasons"], [1, 2, 3])
        self.assertEqual(got["season_pages"], [self.BASE + "/dizi/alpha-show/sezon-1", self.BASE + "/dizi/alpha-show/sezon-3"])
        self.assertEqual((got["first"], got["last"]), ((1, 1), (2, 3)))

    def test_example_4_every_episode_link_and_the_majority_series(self):
        links = "".join(f'<a href="/alpha-show-1-sezon-{e}-bolum-izle-full-tek-parca/">x</a>' for e in (1, 2, 3, 4))
        html = ('<html><body>' + links + '<a href="/beta-1-sezon-1-bolum-izle-full-tek-parca/">b</a>'
                '<a href="/beta-1-sezon-2-bolum-izle-full-tek-parca/">b</a><a href="/hakkinda/">about</a></body></html>')
        got = self.inventory(html, self.BASE + "/diziler/alpha-show-izle/", self.specs()[3])
        self.assertEqual([(e["season"], e["episode"]) for e in got["video_sources"]], [(1, 1), (1, 2), (1, 3), (1, 4)])
        self.assertTrue(any("başka diziye ait" in w for w in got["warnings"]), got["warnings"])

    def test_skill_and_references_teach_the_series_pages_path(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for needle in ("**Series pages**", "`series_page:`", "references/series-page.md", "`episode_links[]`", "series.hint",
                       "series_inventory_ok", "kind: episode", "needs series inventory", "default_season: 1", "NO `episode_source`",
                       "`episode_links`", "episode_url_regex", "`series: {checked, with_episodes, samples[]}`"):
            self.assertIn(needle, text, needle)
        self.assertLess(text.index("**Playable chain**"), text.index("**Series pages**"))
        self.assertLess(text.index("**Series pages**"), text.index("**Collections (home sections)**"))
        self.assertIn("A card that goes straight to an EPISODE page uses `episode_source`", text)
        schema_text = (REFS / "config-schema.md").read_text(encoding="utf-8")
        self.assertIn("| `series_page` |", schema_text)
        forbidden = schema_text.split("Do NOT write:", 1)[1].split("\n\n", 1)[0]
        self.assertNotIn("`series_page`", forbidden.split("(`series_page` is allowed")[0])   # no longer forbidden ...
        self.assertIn("`tab_selector`", schema_text)   # ... except the module-specific keys of the hand-built site
        quality = (REFS / "quality.md").read_text(encoding="utf-8")
        self.assertIn("| `series_inventory_ok` | >= %g |" % sb.MIN_SERIES_INVENTORY_RATIO, quality)
        self.assertIn("| `series_inventory_ok` | `series.samples[]`", quality.split("## What fixes what")[1])
        normalize_text = (REFS / "normalize.md").read_text(encoding="utf-8")
        self.assertIn("`series-page.md`", normalize_text)
        self.assertIn("needs series inventory", normalize_text)
        self.assertEqual(sb.SERIES_HINT, "cards link to series pages: add series_page (see references/series-page.md)")


SEARCH_BASE = "https://www.example-film.com"


class _CurlReply:
    def __init__(self, status=200, headers=None, body=b""):
        self.status_code, self.headers, self.body = status, headers or {}, body


class _CurlSession:
    """Scripted ``curl_cffi`` session (the transport of a POST search): one reply per request, every request recorded."""

    def __init__(self, *replies):
        from types import SimpleNamespace
        self.replies, self.calls, self.closed = list(replies), [], False
        self.cookies = SimpleNamespace(set=lambda *a, **k: None)

    def _answer(self, method, url, headers, body, content_callback):
        self.calls.append((method, url, dict(headers or {}), body))
        reply = self.replies.pop(0)
        if reply.body:
            content_callback(reply.body)
        from types import SimpleNamespace
        return SimpleNamespace(status_code=reply.status_code, headers=reply.headers)

    def get(self, url, *, headers=None, timeout=None, allow_redirects=None, content_callback=None):
        return self._answer("GET", url, headers, None, content_callback)

    def post(self, url, *, headers=None, timeout=None, allow_redirects=None, content_callback=None, data=None, json=None):
        return self._answer("POST", url, headers, data if data is not None else json, content_callback)

    def close(self):
        self.closed = True


class SearchReference(unittest.TestCase):
    """``references/search.md`` + the skill's "Search" step: the key table comes from ``search_generic.SPEC_KEYS``, every example
    validates with ``search_generic.validate_spec`` and is really run by the engine against a sample answer."""

    def reference(self):
        return (REFS / "search.md").read_text(encoding="utf-8")

    def specs(self):
        """The ``search`` mappings of the reference's yaml blocks, in order."""
        return [yaml.safe_load(b)["search"] for b in FENCE.findall(self.reference()) if "search" in yaml.safe_load(b)]

    def setUp(self):
        from app.scraper import search_generic
        search_generic.clear_cache()
        patcher = patch("socket.getaddrinfo", return_value=PUBLIC_DNS)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_get(self, spec, html):
        """The engine on ``spec`` over a canned answer; (items, the recorded ``impersonated_get`` request)."""
        from app.scraper import config as scfg, fetch, search_generic as sg
        cfg = scfg.SiteConfig(site_id="demo", data={"base_url": SEARCH_BASE, "search": spec}, path="")
        with patch.object(fetch, "impersonated_get", return_value=fetch.ImpersonatedPage(html, SEARCH_BASE + "/", 200)) as get:
            return sg.search(cfg, "dark", 20), get

    def run_session(self, spec, *replies):
        from app.scraper import config as scfg, search_generic as sg
        cfg = scfg.SiteConfig(site_id="demo", data={"base_url": SEARCH_BASE, "search": spec}, path="")
        session = _CurlSession(*replies)
        with patch("curl_cffi.requests.Session", return_value=session):
            return sg.search(cfg, "dark", 20), session

    def test_the_key_table_is_generated_from_the_engine_keys(self):
        from app.scraper import search_generic
        text = self.reference()
        table = text.split("<!-- BEGIN GENERATED search-keys", 1)[1].split("<!-- END GENERATED search-keys -->")[0]
        for key in search_generic.SPEC_KEYS:
            self.assertIn("| `%s` |" % key, table, key)
        self.assertEqual(set(gen.SEARCH_KEY_DOCS), set(search_generic.SPEC_KEYS))
        self.assertIn("| `url` | yes |", table)
        self.assertIn("| `fields` | yes |", table)
        tampered = text.replace("| `results_path` |", "| `result_path` |", 1)
        self.assertNotEqual(tampered, text)
        self.assertEqual(gen.replace_block(tampered, "search-keys", gen.render_search_keys()), text)

    def test_a_new_engine_key_without_a_doc_line_breaks_the_generator(self):
        from app.scraper import search_generic
        with patch.object(search_generic, "SPEC_KEYS", search_generic.SPEC_KEYS | {"brand_new_key"}):
            with self.assertRaises(ValueError) as ctx:
                gen.render_search_keys()
        self.assertIn("brand_new_key", str(ctx.exception))

    def test_every_example_validates_and_the_set_is_complete(self):
        from app.scraper import config as scfg, search_generic
        specs = self.specs()
        self.assertEqual(len(specs), 4)
        for index, spec in enumerate(specs):
            self.assertEqual(search_generic.validate_spec(spec, SEARCH_BASE), [], "example %d" % (index + 1))
            cfg = scfg.SiteConfig(site_id="demo", data={"base_url": SEARCH_BASE, "search": spec}, path="")
            self.assertEqual(cfg.search, spec, "the config loader keeps example %d" % (index + 1))
        used = set().union(*[set(s) for s in specs])
        self.assertTrue({"url", "method", "form", "json", "headers", "fetch", "format", "row_selector", "fields", "results_path"} <= used)
        self.assertEqual([s.get("method", "GET") for s in specs], ["GET", "POST", "POST", "GET"])
        self.assertEqual([s.get("format", "html") for s in specs], ["html", "html", "json", "html"])

    def test_example_1_wordpress_get_html_with_lazy_posters(self):
        html = ('<div class="search-results">'
                '<article class="post"><h2 class="entry-title"><a href="https://www.example-film.com/film/dark-city/">Dark City</a></h2>'
                '<img data-src="/up/dark-city.jpg" src="/up/lazy.gif"><span class="year">(1998)</span></article>'
                '<article class="post"><h2 class="entry-title"><a href="/dizi/dark/">Dark</a></h2><img src="/up/dark.jpg"><span class="year">2017</span></article>'
                '</div><article class="post"><h2 class="entry-title"><a href="/not-a-result/">Sidebar</a></h2></article>')
        items, get = self.run_get(self.specs()[0], html)
        self.assertEqual(get.call_args.args[0], SEARCH_BASE + "/?s=dark")
        self.assertEqual([(i["title"], i["detail_url"], i["poster_url"], i["year"]) for i in items], [
            ("Dark City", SEARCH_BASE + "/film/dark-city/", SEARCH_BASE + "/up/dark-city.jpg", 1998),   # data-src before the lazy src
            ("Dark", SEARCH_BASE + "/dizi/dark/", SEARCH_BASE + "/up/dark.jpg", 2017)])   # the row selector is scoped to the results

    def test_example_2_post_form(self):
        html = ('<ul class="sonuclar"><li><a class="baslik" href="/dizi/dark-izle/">Dark</a><img src="/p/dark.jpg"></li>'
                '<li><a class="baslik" href="/film/dark-city-izle/">Dark City</a></li></ul>')
        items, session = self.run_session(self.specs()[1], _CurlReply(200, {}, b"<html>home</html>"), _CurlReply(200, {}, html.encode()))
        self.assertEqual([c[0] for c in session.calls], ["GET", "POST"])   # one page view for the cookies, then the form
        method, url, headers, body = session.calls[1]
        self.assertEqual((url, body), (SEARCH_BASE + "/arama", {"q": "dark", "tur": "tumu"}))
        self.assertEqual((headers["Origin"], headers["Referer"]), (SEARCH_BASE, SEARCH_BASE + "/"))
        self.assertEqual([(i["title"], i["detail_url"], i["poster_url"]) for i in items],
                         [("Dark", SEARCH_BASE + "/dizi/dark-izle/", SEARCH_BASE + "/p/dark.jpg"),
                          ("Dark City", SEARCH_BASE + "/film/dark-city-izle/", None)])
        self.assertTrue(session.closed)

    def test_example_3_json_endpoint(self):
        payload = {"data": {"items": [{"name": "Dark", "slug": "dark-izle", "poster": "dark.jpg", "year": 2017},
                                      {"name": "Dark City", "slug": "dark-city", "poster": "", "year": "1998"},
                                      {"name": "No slug"}]}}
        items, session = self.run_session(self.specs()[2], _CurlReply(200, {}, b"home"),
                                          _CurlReply(200, {"content-type": "application/json"}, json.dumps(payload).encode()))
        method, url, _headers, body = session.calls[1]
        self.assertEqual((method, url, body), ("POST", SEARCH_BASE + "/api/search", {"keyword": "dark", "type": "all"}))
        self.assertEqual([(i["title"], i["detail_url"], i["poster_url"], i["year"]) for i in items], [
            ("Dark", SEARCH_BASE + "/dizi/dark-izle", SEARCH_BASE + "/uploads/dark.jpg", 2017),
            ("Dark City", SEARCH_BASE + "/dizi/dark-city", None, 1998)])

    def test_example_4_ajax_fragment_with_a_referer(self):
        html = ('<a class="arama-sonuc" href="/dizi/dark-izle"><img src="/p/dark.jpg"><span class="ad">Dark</span></a>'
                '<a class="arama-sonuc" href="/film/dark-city-izle"><span class="ad">Dark City</span></a>')
        items, get = self.run_get(self.specs()[3], html)
        self.assertEqual(get.call_args.args[0], SEARCH_BASE + "/ajax/ara?kelime=dark")
        headers = get.call_args.kwargs["headers"]
        self.assertEqual((headers["Referer"], headers["X-Requested-With"]), (SEARCH_BASE + "/", "XMLHttpRequest"))
        self.assertEqual([(i["title"], i["detail_url"]) for i in items],
                         [("Dark", SEARCH_BASE + "/dizi/dark-izle"), ("Dark City", SEARCH_BASE + "/film/dark-city-izle")])

    def test_the_sandbox_judges_an_example_end_to_end(self):
        """The reference's example 1 as the ``search:`` of a draft: ``_run_search`` finds the list item (``found_known``)."""
        from app.scraper import fetch
        data = {"base_url": SEARCH_BASE, "search": self.specs()[0],
                "normalize": {"host": "www.example-film.com", "key": {"from": ["detail_url"], "regex": "/(?:film|dizi)/(?P<slug>[^/]+)", "template": "{slug}"},
                              "type": "movie"}}
        known = {"title": "Dark City", "detail_url": "/film/dark-city/"}
        html = ('<div class="search-results"><article class="post"><h2 class="entry-title"><a href="/film/dark-city/">Dark City</a></h2>'
                '<img src="/up/a.jpg"></article></div>')
        with patch.object(fetch, "impersonated_get", return_value=fetch.ImpersonatedPage(html, SEARCH_BASE + "/", 200)):
            block = sb._run_search(data, "Dark City", known)
        self.assertEqual((block["count"], block["found_known"], block["normalize_ok_ratio"]), (1, True, 1.0))

    def test_skill_and_references_teach_the_search_step(self):
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        flat = " ".join(skill.split())
        self.assertIn("`references/search.md`", skill)
        self.assertTrue((REFS / "search.md").is_file())
        self.assertLess(skill.index("**Player step"), skill.index("**Search (the site's live search"))
        self.assertLess(skill.index("**Search (the site's live search"), skill.index("10. `submit_draft("))
        step = " ".join(skill.split("**Search (the site's live search, `search:`).**", 1)[1].split("10. `submit_draft(", 1)[0].split())
        for needle in ("`test_search(yaml_text, page_id)`", "`found_known: true`", "at most 6 rounds", "`search_ok`", "never keep a block with a guessed URL",
                       "`form: {<input name>: \"{query}\"}`", "grep_page(page_id, \"ajax|/api/|search|arama\")", "`format: json`",
                       "write no `search:` block and say so in `notes`", "Skip it when the site has no search form"):
            self.assertIn(needle, step, needle)
        for needle in ("`test_search(yaml_text, query?, page_id?, detail_page_id?)`", "search-hint:", "`found_known: true`",
                       "A `search:` block comes only from a search form or endpoint you saw", "criterion `search_ok`"):
            self.assertIn(needle, flat, needle)
        self.assertIn("search feature's job", flat)
        text = " ".join(self.reference().split())
        for needle in ("found_known", "normalize_ok_ratio", "test_search(yaml_text, query?, page_id?)", "Referer", "fetch: browser", "grep_page(page_id",
                       "no search block", "the SITE'S OWN host", "No code, no crawling"):
            self.assertIn(needle, text, needle)
        quality = " ".join((REFS / "quality.md").read_text(encoding="utf-8").split())
        self.assertIn("`search.md`", quality)
        self.assertIn("no search block: this site will not be searchable", quality)

    def test_the_tool_list_has_test_search_for_onboarding_only(self):
        from app.scraper import pi_agent
        self.assertEqual(list(pi_agent.ONBOARD_TOOLS), TOOLS)
        self.assertIn("test_search", pi_agent.ONBOARD_TOOLS)
        self.assertNotIn("test_search", pi_agent.REPAIR_TOOLS)   # a repair never touches search:


class CollectionsReference(unittest.TestCase):
    """``references/collections.md`` and the skill's collections step: examples pass the sandbox's collections validator."""

    def examples(self):
        text = (REFS / "collections.md").read_text(encoding="utf-8")
        return [yaml.safe_load(body) for body in FENCE.findall(text)]

    def test_every_example_passes_the_collections_validator(self):
        examples = self.examples()
        self.assertGreaterEqual(len(examples), 4)
        roles = set()
        for index, data in enumerate(examples):
            pairs, errors, warnings = sb._check_collections(data, "")
            self.assertEqual((errors, warnings), ([], []), "example %d" % index)
            self.assertTrue(pairs)
            for spec, entry in pairs:
                self.assertEqual(entry["errors"], [])
                roles.add(spec["role"])
                want = ("category_%s_%s" % (spec["category"], data["site_id"]) if spec["role"] == "category"
                        else "%s_%s" % (spec["role"], data["site_id"]))
                self.assertEqual(spec["id"], want)
        self.assertEqual(roles, set(sb.ONBOARD_ROLES))   # between them the examples show every role onboarding writes

    def test_examples_do_not_teach_catalogue_roles(self):
        for data in self.examples():
            for spec in data["collections"]:
                self.assertIn(spec["role"], sb.ONBOARD_ROLES)

    def test_a_catalogue_role_gets_a_warning(self):
        data = {"site_id": "x1", "base_url": "https://x.example",
                "collections": [{"id": "catalog_x1", "title": "Hepsi", "path": "/hepsi", "role": "catalog"}]}
        _pairs, errors, warnings = sb._check_collections(data, "")
        self.assertEqual(errors, [])
        self.assertTrue(any("whole catalogue" in w for w in warnings))

    def test_skill_steps(self):
        text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("references/collections.md", text)
        self.assertTrue((SKILL_DIR / "references" / "collections.md").is_file())
        self.assertLess(text.index("**Understand the site.**"), text.index("**Collections (home sections)**"))
        self.assertLess(text.index("**Collections (home sections)**"), text.index("**Player step"))
        for needle in ("`nav_links`", "`sections`", "collections: true", "<role>_<site_id>", "search-hint:", "At most 6 rounds"):
            self.assertIn(needle, text, needle)
        for role in sb.ONBOARD_ROLES:
            self.assertIn("`%s`" % role, text)
        # never a catalogue: the skill says so and does not map "all series" to a role
        self.assertIn("Never write an\n      \"all series / all films\"", text)
        self.assertNotIn("`catalog`", text)
        self.assertNotIn("`new`", text)

    def test_latest_series_and_latest_episodes_are_told_apart(self):
        from app.scraper import collections as col
        self.assertIn("latest_series", col.ROLES)
        self.assertIn("latest_series", col.HOME_ROLES)
        self.assertEqual(col.list_id("latest_series", "ornekdizi"), "latest_series_ornekdizi")
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        refs = (REFS / "collections.md").read_text(encoding="utf-8")
        for text in (skill, refs):
            self.assertIn("`latest_series`", text)
        self.assertIn("`latest_series` (Son eklenen diziler / Yeni diziler / Yeni eklenen diziler / Yeni başlayan diziler", skill)
        self.assertIn("Yeni eklenen BÖLÜMLER / Son bölümler", skill)
        self.assertIn("| Son eklenen diziler, Yeni diziler, Yeni eklenen diziler, Yeni başlayan diziler | `latest_series` |", refs)
        self.assertIn("| Yeni eklenen bölümler, Son bölümler, Güncellenen diziler | `latest_episodes` |", refs)
        self.assertIn("| `latest_series` | ranking signal for the home slider and the trend rows", refs)   # the generated role table (from ROLES)
        self.assertIn("| `trending` | feeds the 'Haftanın Trendleri' rows", refs)
        self.assertIn("| `noteworthy_movies` | feeds the 'Dikkate Değer Filmler' row", refs)
        self.assertIn("Never write `role: new` or `role: catalog`", " ".join(refs.split()))
        self.assertNotIn("`role: new`", skill)

    def test_engine_facts_teach_item_limit_and_no_pagination(self):
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## Engine facts", skill)
        self.assertLess(skill.index("## Engine facts"), skill.index("## Rules"))
        facts = " ".join(skill.split("## Engine facts", 1)[1].split("## Rules", 1)[0].split())
        for needle in ("at most `item_limit` items", "default 30, max 200", "NO pagination", "all N titles",
                       "beyond what `test_config` reports", "ingest: {item_limit, list_items_on_page, would_ingest}",
                       "search feature's job"):
            self.assertIn(needle, facts, needle)
        quality = (REFS / "quality.md").read_text(encoding="utf-8")
        self.assertIn("## Engine facts", quality)
        self.assertIn("NO pagination", " ".join(quality.split()))
        self.assertIn("ingest.would_ingest", quality)
        schema_text = (REFS / "config-schema.md").read_text(encoding="utf-8")
        self.assertIn("| `item_limit` | no |", schema_text)
        self.assertIn("1..200", schema_text)
        self.assertIn("default 30", schema_text)
        self.assertNotIn("`item_limit`, `obscura_*`", schema_text)   # no longer a forbidden key
        from app.library import ingest
        self.assertEqual(ingest.COLLECTION_LIMIT, 30)
        self.assertEqual(sb.ITEM_LIMIT_MAX, 200)


ADMIN_DIR = Path(__file__).resolve().parent.parent / "app" / "static" / "admin"
ADMIN_JS = ADMIN_DIR / "onboard.js"
DRAFT_ID = "od_0123456789ab"
#: loads admin/onboard.js against a stub DOM + fetch + a tiny fake server (draft + event list; message / cancel / save change it).
#: per scenario {hash, health, health_error, drafts, draft, events, actions:[{click|key|set|submit|tick|server|hidden|wait}]}:
#: a snapshot of the stub elements (html / value / disabled / hidden / children) + the non-GET calls + the GET urls + the clipboard
UI_HARNESS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const realSetTimeout = setTimeout;
const wait = (ms) => new Promise((r) => realSetTimeout(r, ms));
const clone = (x) => (x === undefined ? null : JSON.parse(JSON.stringify(x)));
const IDS = ["ob-root", "ob-listv", "ob-detv", "ob-health", "ob-start", "ob-why", "ob-newbtn", "ob-newp", "ob-sites", "ob-drafts", "ob-estart", "ob-ewhy", "ob-ehint", "ob-rn",
  "ob-modal", "ob-purge-line", "ob-top", "ob-host", "ob-st", "ob-time", "ob-cancel", "ob-note",
  "ob-headline", "ob-ask", "ob-ask-hint", "ob-steps", "ob-log", "ob-yaml", "ob-fb", "ob-send", "ob-fbh", "ob-sv-info", "ob-sv-panel",
  "ob-sid", "ob-dn", "ob-en", "ob-scan", "ob-savebtn", "ob-sid-err", "toasts"];
async function run(sc) {
  const els = {}, listeners = {}, calls = [], copied = [], confirms = [];
  let tickFn = null, prevented = 0;
  const server = { draft: clone(sc.draft), events: clone(sc.events || []), sites: clone(sc.sites || []), health: clone(sc.health || {}), saveFailed: false };
  const mk = (id) => {
    const e = {
      id, value: "", textContent: "", className: "", title: "", style: {}, disabled: false, checked: false, readOnly: false, childNodes: [], scrollHeight: 0,
      scrollTop: 0, clientHeight: 0, parentNode: null, _h: "", attrs: {},
      get innerHTML() { return this._h; }, set innerHTML(v) { this._h = v; if (v === "") this.childNodes.length = 0; },
      classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, toggle(c, f) { f ? this._s.add(c) : this._s.delete(c); }, contains(c) { return this._s.has(c); } },
      addEventListener(t, f) { (listeners[id] ||= {})[t] = f; },
      appendChild(c) { c.parentNode = e; e.childNodes.push(c); },
      removeChild(c) { const i = e.childNodes.indexOf(c); if (i >= 0) e.childNodes.splice(i, 1); c.parentNode = null; },
      get firstChild() { return e.childNodes[0]; },
      closest() { return null; }, select() {}, setAttribute(n, v) { e.attrs[n] = v; }, getAttribute() { return null; },
    };
    return e;
  };
  const el = (id) => els[id] || (els[id] = mk(id));
  global.document = { getElementById: el, hidden: false, createElement: () => mk("_new"), addEventListener() {}, dispatchEvent() {}, body: { appendChild() {}, removeChild() {} } };
  global.window = {}; global.location = { hash: sc.hash === undefined ? "#onboard/" + "od_0123456789ab" : sc.hash }; global.history = { replaceState() {} };
  global.CustomEvent = function () {};
  global.confirm = (m) => { confirms.push(m); return sc.confirm !== false; };
  global.setInterval = (fn) => { tickFn = fn; return 1; };
  global.setTimeout = () => 0;                       // toast removal etc.: never fires, so the snapshot sees the toasts
  Object.defineProperty(globalThis, "navigator", { value: { clipboard: { writeText: (t) => { copied.push(t); return Promise.resolve(); } } }, configurable: true, writable: true });
  global.fetch = (url, opt) => {
    opt = opt || {};
    const method = opt.method || "GET";
    calls.push({ url, method, body: opt.body });
    const reply = (body, status = 200) => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
    const site = (id) => server.sites.find((x) => x.site_id === id) || {};
    let m;
    if (method === "GET" && /\/api\/ops\/sites\/manage$/.test(url)) return sc.sites_error ? reply({ error: { message: sc.sites_error } }, 500) : reply({ sites: clone(server.sites) });
    if (method === "GET" && (m = /\/api\/ops\/sites\/([a-z0-9_]+)\/config$/.exec(url))) {
      if (sc.config_error) return reply({ error: { code: "not_found", message: sc.config_error } }, 404);
      return reply({ site_id: m[1], version: site(m[1]).version || 3, yaml_text: (sc.configs || {})[m[1]] || "playback: video\nbase_url: https://x.example\n",
        versions: [{ version: 2, updated_at: "2026-01-01T00:00:00Z" }, { version: 1, updated_at: "2025-12-01T00:00:00Z" }], baseline: {} });
    }
    if (method === "GET" && /\/health$/.test(url)) return sc.health_error ? reply({ error: { message: sc.health_error } }, 500) : reply(clone(server.health));
    if (method === "GET" && (m = /events_after=(\d+)/.exec(url))) return reply({ draft: clone(server.draft), events: clone(server.events.slice(+m[1])), events_total: server.events.length });
    if (method === "GET") return reply({ drafts: clone(sc.drafts || []) });
    if (method === "DELETE" && (m = /\/api\/ops\/sites\/([a-z0-9_]+)\?purge=(\d)$/.exec(url))) {
      if (sc.delete_error) return reply({ error: { code: "busy", message: sc.delete_error } }, 409);
      server.sites = server.sites.filter((x) => x.site_id !== m[1]);
      return reply({ deleted: true, files: [], purged: { source_items: 4, video_sources: 280, library_items: 470 }, tombstone: true });
    }
    if (method === "DELETE") return reply({ ok: true });
    const body = JSON.parse(opt.body || "{}");
    if ((m = /\/api\/ops\/sites\/([a-z0-9_]+)\/rename$/.exec(url))) { const s = site(m[1]); s.display_name = body.display_name; s.version += 1; return reply({ version: s.version }); }
    if ((m = /\/api\/ops\/sites\/([a-z0-9_]+)\/rollback$/.exec(url))) { const s = site(m[1]); s.version -= 1; return reply({ ok: true, version: s.version }); }
    if (/\/message$/.test(url)) { server.events.push({ kind: "user", text: body.text, t: "2026-01-01T00:02:00Z" }); server.draft.status = "running"; return reply({ ok: true }); }
    if (/\/cancel$/.test(url)) {
      server.health.running = null;                    // the job is over either way
      if (sc.cancel_error) return reply({ error: { code: "not_running", message: sc.cancel_error } }, 409);
      if (server.draft) server.draft.status = "cancelled";
      return reply({ ok: true });
    }
    if (/\/save$/.test(url)) {
      if (sc.save_error) return reply({ error: { message: sc.save_error } }, 400);
      if (sc.save_fail_once && !server.saveFailed) { server.saveFailed = true; const f = sc.save_fail_once; return reply({ error: { code: f.code, message: f.message } }, f.status || 409); }
      server.draft.status = "saved"; server.draft.saved_site_id = body.site_id;
      return reply({ site_id: body.site_id, version: sc.save_version || 1, scan_started: sc.scan_started !== false && body.scan_now !== false, mode: server.draft.mode || "new" });
    }
    if (sc.start_error) return reply({ error: { code: "already_running", message: sc.start_error } }, 409);
    return reply({ draft: { id: "od_0123456789ab" } });   // POST /api/ops/onboard/ (start)
  };
  el("tab-onboard").classList.add("hidden");           // the script must not start by itself
  (0, eval)(src);
  el("tab-onboard").classList.remove("hidden");
  window.dzTabHooks.onboard();
  await wait(40);
  for (const a of sc.actions || []) {
    if (a.click) {
      const attrs = { "data-ob": a.click.act, "data-id": a.click.id || null, "data-step": a.click.step || null, "data-site": a.click.site || null };
      listeners["ob-root"].click({ target: { closest: () => ({ getAttribute: (n) => (n in attrs ? attrs[n] : null), disabled: !!a.click.disabled }) } });
    } else if (a.key) {
      listeners["ob-root"].keydown({ target: { id: a.key.id || "ob-fb" }, key: a.key.key, shiftKey: !!a.key.shift, isComposing: false, preventDefault() { prevented++; } });
    } else if (a.set) {
      const e = el(a.set.id);
      if ("value" in a.set) { e.value = a.set.value; if (listeners[a.set.id] && listeners[a.set.id].input) listeners[a.set.id].input({}); }
      if ("checked" in a.set) {
        e.checked = a.set.checked;
        const f = (listeners[a.set.id] && listeners[a.set.id].change) || (listeners["ob-root"] && listeners["ob-root"].change);   // checkboxes of the dynamic panels: delegated on the root
        if (f) f({ target: { id: a.set.id, checked: e.checked, value: e.value } });
      }
    } else if (a.submit) {
      listeners["ob-form"].submit({ preventDefault() {} });
    } else if (a.tick) {
      for (let i = 0; i < a.tick; i++) { tickFn(); await wait(15); }
    } else if (a.server) {
      if (server.draft) Object.assign(server.draft, clone(a.server.draft || {}));
      if (a.server.health !== undefined) server.health = clone(a.server.health);
      for (const ev of a.server.push || []) server.events.push(clone(ev));
    } else if (a.prop) {
      Object.assign(el(a.prop.id), a.prop.props);
    } else if (a.hidden !== undefined) {
      document.hidden = !!a.hidden;
    }
    await wait(a.wait || 30);
  }
  const snap = {};
  for (const id of IDS) {
    const e = els[id];
    if (e) snap[id] = { html: e.innerHTML, text: e.textContent, value: e.value, disabled: e.disabled, checked: e.checked, readOnly: e.readOnly, title: e.title, attrs: e.attrs, scrollTop: e.scrollTop,
      hidden: e.classList.contains("hidden"), children: e.childNodes.map((c) => ({ cls: c.className, html: c.innerHTML, text: c.textContent })) };
  }
  return { els: snap, copied, confirms, prevented, status: server.draft && server.draft.status, toasts: els["toasts"] ? els["toasts"].childNodes.map((c) => c.textContent) : [],
    posts: calls.filter((c) => c.method !== "GET").map((c) => ({ url: c.url, method: c.method, body: JSON.parse(c.body || "{}") })),
    gets: calls.filter((c) => c.method === "GET").map((c) => c.url) };
}
(async () => {
  const out = [];
  for (const sc of JSON.parse(process.argv[3])) out.push(await run(sc));
  process.stdout.write(JSON.stringify(out) + "\n", () => process.exit(0));   // flushed first: the snapshot is bigger than a pipe buffer
})();
"""

#: the six steps of the pipeline contract (GET /api/ops/onboard/{id} -> draft.pipeline), in order
PIPE_STEPS = [
    ("home", "Ana sayfa bölümleri", "Ana sayfada bölümler bulundu mu?"),
    ("links", "Dizi/film sayfası bağlantıları", "Dizi ve film sayfalarının bağlantıları bulundu mu?"),
    ("info", "Dizi bilgisi: özet, oyuncu, sezon, bölüm", "Dizi bilgisi, sezonlar, bölümler ve oyuncular alındı mı?"),
    ("player", "Bölüm sayfası ve oynatıcı", "Bölüm sayfası açılıp oynatıcı bilgisi alındı mı?"),
    ("stream", "Video akışı", "Oynatıcıdan video akışı çözüldü mü?"),
    ("search", "Site araması", "Sitenin kendi araması çalışıyor mu?"),
]
PIPE_APP = {"rows": [{"key": "trending", "title": "Haftanın Trendleri", "count": 12}, {"key": "noteworthy_movies", "title": "Dikkate Değer Filmler", "count": 30},
                      {"key": "series", "title": "Tüm Diziler", "count": 30}],
            "signals": [{"key": "latest_series", "title": "Yeni diziler", "count": 7}, {"key": "featured", "title": "Öne çıkanlar", "count": 5}],
            "totals": {"series": 458, "movies": 0, "episodes": 120, "ingest_per_list": 30, "playable": "2/3"},
            "note": "Her liste en çok 30 öğe alınır."}


def pipe(states, state="ok", headline="Tüm adımlar çalışıyor", problem_step=None, app=True):
    steps = []
    for (sid, title, question), st in zip(PIPE_STEPS, states):
        steps.append({"id": sid, "title": title, "question": question, "state": st,
                      "summary": "%s özeti & sayılar <b>x</b>" % sid if st in ("ok", "warn", "fail") else "",
                      "numbers": [{"label": "Bölüm", "value": "4"}, {"label": "Dizi", "value": "7"}] if st == "ok" else [],
                      "problem": ("%s sorunu: yaml'a series_page ekle <i>(ipucu)</i>" % sid) if st in ("warn", "fail") else None,
                      "details": [{"label": "Trendler", "value": "12 öğe"}, {"label": "Yol <u>", "value": "/trend"}] if st != "pending" else []})
    return {"overall": {"state": state, "headline": headline, "problem_step": problem_step}, "steps": steps,
            "app": dict(PIPE_APP) if app else None}


def esc(s):
    """What the page's ``esc`` makes of a string."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#39;"))


def ui_draft(status="ready", **kw):
    d = {"id": DRAFT_ID, "url": "https://x.example/", "status": status, "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:01:00Z",
         "site_id_suggestion": "x_example", "yaml_text": "playback: video\nbase_url: https://x.example\n",
         "report": {"passed": True, "criteria": {}, "errors": [], "warnings": []},
         "pipeline": pipe(["ok"] * 5 + ["skipped"])}
    d.update(kw)
    return d


def site_row(site_id="x_example", **kw):
    """One entry of GET /api/ops/sites/manage."""
    d = {"site_id": site_id, "display_name": "X Example", "base_url": "https://x.example", "version": 3, "hand_built": False,
         "auto_scan": {"enabled": True, "interval_hours": 6, "next_scan_at": "2099-01-01T00:00:00Z"},
         "last_run": {"at": "2026-01-01T00:00:00Z", "status": "success", "scraped": 120, "ingested": 118},
         "counts": {"titles": 470, "series": 458, "movies": 12, "episodes": 320, "with_sources": 280},
         "search": True, "providers": ["vidmolly", "okru"], "can_rollback": False, "busy": None}
    d.update(kw)
    return d


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class AdminOnboardPage(unittest.TestCase):
    """The "Siteler" tab: site list (edit / yaml / rename / rollback / delete), new-site panel, drafts, health card; the draft screen (pinned
    save strip + confirmation, pipeline steps, log | yaml, chat). Run by node on a stub DOM."""

    def run_ui(self, *scenarios):
        tmp = tempfile.mkdtemp(prefix="onboard-ui-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        harness = os.path.join(tmp, "ui.js")
        Path(harness).write_text(UI_HARNESS, encoding="utf-8")
        proc = subprocess.run(["node", harness, str(ADMIN_JS), json.dumps(list(scenarios))], capture_output=True, text=True, timeout=90)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def draft_ui(self, draft=None, **kw):
        return self.run_ui({"draft": draft or ui_draft(), **kw})[0]

    @staticmethod
    def step_states(html):
        return re.findall(r'class="obstep st-(\w+)', html)

    # --- list screen (the main screen) ------------------------------------------------------------------------------------

    def test_the_tab_is_called_siteler_and_the_hash_is_the_same(self):
        index = (ADMIN_DIR / "index.html").read_text(encoding="utf-8")
        self.assertRegex(index, r'id="tab-btn-onboard"[^>]*>Siteler</button>')
        self.assertIn('data-tab="onboard"', index)
        self.assertIn('id="tab-onboard"', index)
        lib = (ADMIN_DIR / "library.js").read_text(encoding="utf-8")
        self.assertIn("onboard:'Siteler'", lib)
        self.assertIn("'onboard'", lib)

    def test_the_list_is_the_main_screen_with_a_new_site_button_and_a_closed_form(self):
        got = self.run_ui({"hash": "", "sites": [site_row("yabancidizi", display_name="Yabancı Dizi", hand_built=True, version=7), site_row()], "drafts": []})[0]
        root = got["els"]["ob-root"]["html"]
        for needle in ("Siteler", "+ Yeni site", 'id="ob-newbtn"', 'id="ob-sites"', "Taslaklar", 'id="ob-drafts"', 'id="ob-url"', 'id="ob-hint"', "Site adresi", "Başlat"):
            self.assertIn(needle, root, needle)
        self.assertLess(root.index('id="ob-newbtn"'), root.index('id="ob-sites"'))
        self.assertLess(root.index('id="ob-sites"'), root.index('id="ob-drafts"'))
        self.assertNotIn("Önceki taslaklar", root)
        self.assertIn('<section class="card hidden" id="ob-newp">', root)   # the new-site form is closed by default
        self.assertFalse(got["els"]["ob-listv"]["hidden"])
        self.assertTrue(got["els"]["ob-detv"]["hidden"])
        self.assertTrue(got["els"]["ob-health"]["hidden"])           # healthy: no card at all
        self.assertEqual(got["els"]["ob-health"]["html"], "")
        self.assertFalse(got["els"]["ob-start"]["disabled"])
        self.assertEqual(root.count("<form"), 1)
        self.assertIn("/api/ops/sites/manage", got["gets"])
        names = got["els"]["ob-sites"]["html"]
        self.assertIn("Yabancı Dizi", names)                         # the hand-built ones are listed too
        self.assertIn("X Example", names)

    def test_each_site_row_shows_name_badges_scan_state_counts_and_the_actions(self):
        yd = site_row("yabancidizi", display_name="Yabancı Dizi", base_url="https://yabancidizi.example", version=7, hand_built=True, can_rollback=True)
        plain = site_row("x_example", search=False, auto_scan={"enabled": False, "interval_hours": None, "next_scan_at": None}, last_run=None, providers=[])
        html = self.run_ui({"hash": "", "sites": [yd, plain]})[0]["els"]["ob-sites"]["html"]
        rows = html.split('<li class="obs')[1:]
        self.assertEqual(len(rows), 2)
        a, b = rows
        for needle in ("Yabancı Dizi", "yabancidizi · yabancidizi.example", ">v7<", "elle yapılmış", "arama var", "açık · her 6 sa", "sonraki", "Başarılı", "120 çekildi", "118 işlendi",
                       "458 dizi · 12 film · 320 bölüm · 280 kaynaklı", "vidmolly", "okru", 'data-ob="sedit" data-site="yabancidizi"', 'data-ob="syaml" data-site="yabancidizi"',
                       'data-ob="srename" data-site="yabancidizi"', 'data-ob="srollback" data-site="yabancidizi"', 'data-ob="goto-settings"', 'data-ob="sdel" data-site="yabancidizi"',
                       "Düzenle", "YAML", "Ad değiştir", "Geri al", "Ayarlar", "Sil"):
            self.assertIn(needle, a, needle)
        for needle in ("X Example", "arama yok", "kapalı", "henüz tarama yok", ">v3<", 'data-ob="sdel" data-site="x_example"'):
            self.assertIn(needle, b, needle)
        self.assertNotIn("elle yapılmış", b)
        self.assertNotIn("srollback", b)                                # nothing to roll back
        self.assertNotIn("arama var", b)
        self.assertNotIn("Oynatıcılar", b)
        self.assertNotIn("disabled", a + b)                             # idle sites: every action is open
        names = (a + b)
        self.assertEqual(names.count("elle yapılmış"), 1)

    def test_a_busy_site_shows_the_running_job_and_cannot_be_edited_or_deleted(self):
        html = self.run_ui({"hash": "", "sites": [site_row(busy="scan", can_rollback=True)]})[0]["els"]["ob-sites"]["html"]
        self.assertIn("çalışıyor: tarama", html)
        self.assertIn("Çalışan iş var: tarama", html)               # the reason is written
        for act in ("sedit", "srename", "srollback", "sdel"):
            self.assertRegex(html, r'data-ob="%s" data-site="x_example" disabled' % act)
        self.assertNotRegex(html, r'data-ob="syaml" data-site="x_example" disabled')
        self.assertNotRegex(html, r'data-ob="goto-settings" disabled')
        names = [self.run_ui({"hash": "", "sites": [site_row(busy=b)]})[0]["els"]["ob-sites"]["html"] for b in ("heal", "onboard", "finder")]
        for html, word in zip(names, ("düzeltme", "site ekleme", "kaynak arama")):
            self.assertIn("çalışıyor: " + word, html)

    def test_empty_and_failed_site_list(self):
        empty, failed = self.run_ui({"hash": "", "sites": []}, {"hash": "", "sites_error": "sunucu yok"})
        self.assertIn("Kayıtlı site yok", empty["els"]["ob-sites"]["html"])
        self.assertIn("Site listesi alınamadı: sunucu yok", failed["els"]["ob-sites"]["html"])
        self.assertIn('data-ob="srefresh"', failed["els"]["ob-sites"]["html"])

    def test_new_site_panel_opens_and_closes(self):
        opened, closed, again = self.run_ui(
            {"hash": "", "actions": [{"click": {"act": "new"}}]},
            {"hash": "", "actions": [{"click": {"act": "new"}}, {"click": {"act": "new"}}]},
            {"hash": "", "actions": [{"click": {"act": "new"}}, {"click": {"act": "newclose"}}]})
        self.assertFalse(opened["els"]["ob-newp"]["hidden"])
        self.assertEqual(opened["els"]["ob-newbtn"]["attrs"]["aria-expanded"], "true")
        self.assertTrue(closed["els"]["ob-newp"]["hidden"])
        self.assertEqual(closed["els"]["ob-newbtn"]["attrs"]["aria-expanded"], "false")
        self.assertTrue(again["els"]["ob-newp"]["hidden"])
        self.assertIn('data-ob="newclose"', opened["els"]["ob-root"]["html"])

    def test_health_card_only_on_a_problem_and_start_is_disabled_then(self):
        run = {"draft_id": DRAFT_ID, "url": "https://x.example/liste", "status": "running"}
        good, bad, busy, off, failed, finished, old = self.run_ui(
            {"hash": "", "health": {"pi_ok": True, "skill": {"ok": True}, "extension": {"ok": True}}},
            {"hash": "", "health": {"pi_ok": False, "skill": {"ok": False}}},
            {"hash": "", "health": {"running": run}},
            {"hash": "", "health": {"enabled": False}},
            {"hash": "", "health_error": "sunucu yanıt vermedi"},
            {"hash": "", "health": {"running": {**run, "status": "ready"}}},      # the race: the job has just ended
            {"hash": "", "health": {"running": True}})                            # an old server: no draft id
        self.assertTrue(good["els"]["ob-health"]["hidden"])
        self.assertFalse(good["els"]["ob-start"]["disabled"])
        self.assertFalse(bad["els"]["ob-health"]["hidden"])
        self.assertTrue(bad["els"]["ob-start"]["disabled"])
        for needle in ("pi bulunamadı", "skill dosyası yok", "Yeniden kontrol et"):
            self.assertIn(needle, bad["els"]["ob-health"]["html"], needle)
        self.assertNotIn("extension", bad["els"]["ob-health"]["html"])    # only the failing ones are listed
        self.assertTrue(busy["els"]["ob-start"]["disabled"])
        html = busy["els"]["ob-health"]["html"]
        self.assertIn("Şu an x.example için bir site ekleme işi çalışıyor", html)
        self.assertIn('data-ob="open" data-id="%s"' % DRAFT_ID, html)
        self.assertIn('data-ob="hcancel" data-id="%s"' % DRAFT_ID, html)
        self.assertIn("Yeniden kontrol et", html)
        self.assertNotIn("Başlatılamıyor", html)                            # it is not a failure, the one job just has the slot
        self.assertTrue(off["els"]["ob-start"]["disabled"])
        self.assertIn("site ekleme kapalı", off["els"]["ob-health"]["html"])
        self.assertTrue(failed["els"]["ob-start"]["disabled"])
        self.assertIn("sunucu yanıt vermedi", failed["els"]["ob-health"]["html"])
        self.assertTrue(finished["els"]["ob-health"]["hidden"])             # an ended job does not hold anything
        self.assertFalse(finished["els"]["ob-start"]["disabled"])
        self.assertIn("Şu an bir site ekleme işi çalışıyor", old["els"]["ob-health"]["html"])
        self.assertNotIn('data-ob="hcancel"', old["els"]["ob-health"]["html"])

    def test_a_running_job_card_can_be_opened_or_cancelled_and_clears_itself(self):
        health = {"running": {"draft_id": DRAFT_ID, "url": "https://x.example/", "status": "running"}}
        opened = self.run_ui({"hash": "", "health": health, "draft": ui_draft("running"), "actions": [{"click": {"act": "open", "id": DRAFT_ID}}]})[0]
        self.assertFalse(opened["els"]["ob-detv"]["hidden"])
        cancelled = self.run_ui({"hash": "", "health": health, "actions": [{"click": {"act": "hcancel", "id": DRAFT_ID}}]})[0]
        self.assertEqual(cancelled["posts"], [{"url": "/api/ops/onboard/%s/cancel" % DRAFT_ID, "method": "POST", "body": {}}])
        self.assertTrue(cancelled["els"]["ob-health"]["hidden"])             # the next health check says nothing runs
        self.assertFalse(cancelled["els"]["ob-start"]["disabled"])
        late = self.run_ui({"hash": "", "health": health, "cancel_error": "draft is ready, nothing to cancel", "actions": [{"click": {"act": "hcancel", "id": DRAFT_ID}}]})[0]
        self.assertIn("İş zaten bitmiş", late["toasts"][0])                  # it ended in the meantime: said, and the card goes
        self.assertTrue(late["els"]["ob-health"]["hidden"])
        ticking = self.run_ui({"hash": "", "health": health, "actions": [{"server": {"health": {"running": None}}}, {"tick": 4}]})[0]
        self.assertTrue(ticking["els"]["ob-health"]["hidden"])               # the list polls the health while a job holds the slot
        self.assertFalse(ticking["els"]["ob-start"]["disabled"])

    def test_previous_drafts_list_has_url_status_headline_date_open_and_delete(self):
        rows = []
        for i, (status, headline, state) in enumerate([("ready", "Tüm adımlar çalışıyor", "ok"), ("running", "Ajan çalışıyor", "running"),
                                                       ("needs_input", "Sorun 2. adımda: afişler eksik", "warn"), ("failed", "Sorun 3. adımda: bölüm listesi alınamadı", "fail"),
                                                       ("cancelled", "Henüz sonuç yok.", "idle"), ("saved", "Tüm adımlar çalışıyor", "ok")]):
            rows.append({"id": "od_%012x" % i, "url": "https://site%d.example/liste" % i, "status": status, "created_at": "2026-01-01T00:00:00Z",
                         "overall": {"state": state, "headline": headline}})
        rows[0].update({"mode": "edit", "edit_site_id": "site0"})
        got = self.run_ui({"hash": "", "drafts": rows})[0]["els"]["ob-drafts"]["html"]
        for needle in ("site0.example", "https://site3.example/liste", "Hazır", "Çalışıyor", "Soru bekliyor", "Başarısız", "İptal", "Kaydedildi",
                       "Sorun 2. adımda: afişler eksik", "Sorun 3. adımda: bölüm listesi alınamadı", "obdh ok", "obdh warn", "obdh bad", "obdh run", "düzenleme: site0"):
            self.assertIn(needle, got, needle)
        self.assertEqual(got.count('data-ob="open"'), 6)
        self.assertEqual(got.count('data-ob="del"'), 6)
        self.assertEqual(got.count("disabled"), 1)               # a running draft cannot be deleted
        self.assertIn('data-ob="open" data-id="od_000000000003"', got)
        empty = self.run_ui({"hash": "", "drafts": []})[0]["els"]["ob-drafts"]["html"]
        self.assertIn("Henüz taslak yok", empty)

    def test_start_posts_the_address_and_the_note_and_opens_the_draft(self):
        got = self.run_ui({"hash": "", "draft": ui_draft("running"), "actions": [
            {"click": {"act": "new"}},
            {"set": {"id": "ob-url", "value": "ornek-site.com"}}, {"set": {"id": "ob-hint", "value": "yalnızca filmler"}}, {"submit": True}]})[0]
        self.assertEqual(got["posts"], [{"url": "/api/ops/onboard/", "method": "POST", "body": {"url": "https://ornek-site.com", "hint": "yalnızca filmler"}}])
        self.assertFalse(got["els"]["ob-detv"]["hidden"])         # the new draft's screen
        self.assertTrue(got["els"]["ob-listv"]["hidden"])
        self.assertTrue(got["els"]["ob-newp"]["hidden"])          # the form closes once it has started
        none = self.run_ui({"hash": "", "actions": [{"set": {"id": "ob-url", "value": "ftp://x"}}, {"submit": True}]})[0]
        self.assertEqual(none["posts"], [])
        self.assertIn("Geçerli bir adres gir", none["toasts"][0])
        busy = self.run_ui({"hash": "", "start_error": "another onboarding run is in progress", "actions": [{"set": {"id": "ob-url", "value": "https://x.example"}}, {"submit": True}]})[0]
        self.assertIn("another onboarding run", busy["toasts"][0])
        self.assertEqual(len([u for u in busy["gets"] if u.endswith("/health")]), 2)   # the card is refreshed: what runs, with Aç / İptal

    # --- list screen: the row actions -------------------------------------------------------------------------------------

    def test_edit_asks_what_should_change_and_posts_a_mode_edit_request(self):
        base = {"hash": "", "sites": [site_row()], "draft": ui_draft("running", mode="edit", edit_site_id="x_example")}
        sedit = {"click": {"act": "sedit", "site": "x_example"}}
        hint = {"set": {"id": "ob-ehint", "value": "  afişler yanlış  "}}
        estart = {"click": {"act": "estart", "site": "x_example"}}
        opened = self.run_ui({**base, "actions": [sedit]})[0]
        html = opened["els"]["ob-sites"]["html"]
        for needle in ("Ne değişsin?", 'id="ob-ehint"', 'id="ob-estart"', 'data-ob="ecancel"'):
            self.assertIn(needle, html, needle)
        self.assertEqual(opened["posts"], [])
        sent = self.run_ui({**base, "actions": [sedit, hint, estart]})[0]
        self.assertEqual(sent["posts"], [{"url": "/api/ops/onboard/", "method": "POST", "body": {"mode": "edit", "site_id": "x_example", "hint": "afişler yanlış"}}])
        self.assertFalse(sent["els"]["ob-detv"]["hidden"])
        self.assertTrue(sent["els"]["ob-listv"]["hidden"])
        empty = self.run_ui({**base, "actions": [sedit, estart]})[0]
        self.assertEqual(empty["posts"], [])
        self.assertIn("Ne değişmesini", empty["toasts"][0])
        cancelled = self.run_ui({**base, "actions": [sedit, {"click": {"act": "ecancel"}}]})[0]
        self.assertNotIn("ob-ehint", cancelled["els"]["ob-sites"]["html"])
        enter = self.run_ui({**base, "actions": [sedit, hint, {"key": {"id": "ob-ehint", "key": "Enter"}}]})[0]
        self.assertEqual([p["body"]["mode"] for p in enter["posts"]], ["edit"])
        self.assertEqual(enter["prevented"], 1)
        blocked = self.run_ui({**base, "health": {"pi_ok": False}, "actions": [sedit, hint, estart]})[0]
        self.assertEqual(blocked["posts"], [])                              # the same health gate as a new site
        self.assertTrue(blocked["els"]["ob-estart"]["disabled"])
        self.assertIn("pi bulunamadı", blocked["els"]["ob-ewhy"]["text"])
        busy = self.run_ui({**base, "start_error": "another onboarding run is in progress", "actions": [sedit, hint, estart]})[0]
        self.assertIn("another onboarding run", busy["toasts"][0])
        self.assertEqual(len([u for u in busy["gets"] if u.endswith("/health")]), 2)

    def test_yaml_button_opens_a_read_only_modal_with_copy(self):
        yaml_text = "playback: video\nbase_url: https://x.example/<b>\n"
        sc = {"hash": "", "sites": [site_row()], "configs": {"x_example": yaml_text}}
        syaml = {"click": {"act": "syaml", "site": "x_example"}}
        got = self.run_ui({**sc, "actions": [syaml]})[0]
        self.assertIn("/api/ops/sites/x_example/config", got["gets"])
        modal = got["els"]["ob-modal"]
        self.assertFalse(modal["hidden"])
        for needle in ("X Example · yaml (v3)", "<pre", "playback: video", "&lt;b&gt;", "Kopyala", 'data-ob="mclose"', "v2", "v1", "Salt okunur"):
            self.assertIn(needle, modal["html"], needle)
        self.assertNotIn("<textarea", modal["html"])
        self.assertEqual(got["posts"], [])
        copied = self.run_ui({**sc, "actions": [syaml, {"click": {"act": "mcopy"}}]})[0]
        self.assertEqual(copied["copied"], [yaml_text])
        closed = self.run_ui({**sc, "actions": [syaml, {"click": {"act": "mclose"}}]})[0]
        self.assertTrue(closed["els"]["ob-modal"]["hidden"])
        self.assertEqual(closed["els"]["ob-modal"]["html"], "")
        esc_key = self.run_ui({**sc, "actions": [syaml, {"key": {"id": "ob-mbox", "key": "Escape"}}]})[0]
        self.assertTrue(esc_key["els"]["ob-modal"]["hidden"])
        err = self.run_ui({**sc, "config_error": "site not found", "actions": [syaml]})[0]
        self.assertIn("Config alınamadı: site not found", err["els"]["ob-modal"]["html"])

    def test_rename_inline_posts_the_new_name_and_reloads_the_list(self):
        sc = {"hash": "", "sites": [site_row()]}
        srename = {"click": {"act": "srename", "site": "x_example"}}
        opened = self.run_ui({**sc, "actions": [srename]})[0]
        self.assertIn('id="ob-rn"', opened["els"]["ob-sites"]["html"])
        self.assertIn('value="X Example"', opened["els"]["ob-sites"]["html"])
        done = self.run_ui({**sc, "actions": [srename, {"set": {"id": "ob-rn", "value": " Yeni Ad "}}, {"click": {"act": "rnsave", "site": "x_example"}}]})[0]
        self.assertEqual(done["posts"], [{"url": "/api/ops/sites/x_example/rename", "method": "POST", "body": {"display_name": "Yeni Ad"}}])
        html = done["els"]["ob-sites"]["html"]
        self.assertIn("Yeni Ad", html)
        self.assertIn(">v4<", html)                                          # a rename is a new version
        self.assertNotIn('id="ob-rn"', html)
        empty = self.run_ui({**sc, "actions": [srename, {"set": {"id": "ob-rn", "value": "  "}}, {"click": {"act": "rnsave", "site": "x_example"}}]})[0]
        self.assertEqual(empty["posts"], [])
        enter = self.run_ui({**sc, "actions": [srename, {"set": {"id": "ob-rn", "value": "Yeni"}}, {"key": {"id": "ob-rn", "key": "Enter"}}]})[0]
        self.assertEqual(len(enter["posts"]), 1)

    def test_rollback_asks_first_and_posts(self):
        sc = {"hash": "", "sites": [site_row(can_rollback=True)]}
        srollback = {"click": {"act": "srollback", "site": "x_example"}}
        ok = self.run_ui({**sc, "actions": [srollback]})[0]
        self.assertEqual(ok["posts"], [{"url": "/api/ops/sites/x_example/rollback", "method": "POST", "body": {}}])
        self.assertIn("X Example", ok["confirms"][0])
        self.assertIn("v2 geri yüklendi", ok["toasts"][0])
        self.assertIn(">v2<", ok["els"]["ob-sites"]["html"])
        no = self.run_ui({**sc, "confirm": False, "actions": [srollback]})[0]
        self.assertEqual(no["posts"], [])

    def test_delete_confirmation_shows_counts_the_purge_box_and_the_hand_built_warning(self):
        hb = site_row("yabancidizi", display_name="Yabancı Dizi", hand_built=True)
        got = self.run_ui({"hash": "", "sites": [hb, site_row()], "actions": [{"click": {"act": "sdel", "site": "yabancidizi"}}]})[0]
        modal = got["els"]["ob-modal"]
        self.assertFalse(modal["hidden"])
        for needle in ("“Yabancı Dizi” silinsin mi?", "Bu işlem geri alınamaz", "470 başlık ve 280 kaynak katalogdan kaldırılacak", 'id="ob-purge" checked',
                       "Kütüphane kayıtlarını da sil", "Elle yapılmış site", "kod modülü repoda kalır", 'data-ob="mdel"', 'data-ob="mclose"'):
            self.assertIn(needle, modal["html"], needle)
        self.assertEqual(got["posts"], [])                                   # nothing is deleted before the confirmation
        plain = self.run_ui({"hash": "", "sites": [site_row()], "actions": [{"click": {"act": "sdel", "site": "x_example"}}]})[0]
        self.assertNotIn("Elle yapılmış site", plain["els"]["ob-modal"]["html"])

    def test_delete_calls_the_endpoint_with_the_purge_choice_and_refreshes_the_list(self):
        sc = {"hash": "", "sites": [site_row()]}
        sdel = {"click": {"act": "sdel", "site": "x_example"}}
        done = self.run_ui({**sc, "actions": [sdel, {"click": {"act": "mdel"}}]})[0]
        self.assertEqual(done["posts"], [{"url": "/api/ops/sites/x_example?purge=1", "method": "DELETE", "body": {}}])
        self.assertTrue(done["els"]["ob-modal"]["hidden"])
        self.assertIn("Silindi: X Example", done["toasts"][0])
        self.assertIn("470 başlık, 280 kaynak kaldırıldı", done["toasts"][0])
        self.assertIn("Kayıtlı site yok", done["els"]["ob-sites"]["html"])
        unchecked = {"set": {"id": "ob-purge", "checked": False}}
        line = self.run_ui({**sc, "actions": [sdel, unchecked]})[0]
        self.assertIn("Kütüphane kayıtları kalır", line["els"]["ob-purge-line"]["text"])   # the sentence follows the box
        kept = self.run_ui({**sc, "actions": [sdel, unchecked, {"click": {"act": "mdel"}}]})[0]
        self.assertEqual(kept["posts"], [{"url": "/api/ops/sites/x_example?purge=0", "method": "DELETE", "body": {}}])
        self.assertNotIn("kaldırıldı", kept["toasts"][0])
        no = self.run_ui({**sc, "actions": [sdel, {"click": {"act": "mclose"}}]})[0]
        self.assertEqual(no["posts"], [])
        self.assertTrue(no["els"]["ob-modal"]["hidden"])
        busy = self.run_ui({**sc, "delete_error": "site is scanning", "actions": [sdel, {"click": {"act": "mdel"}}]})[0]
        self.assertFalse(busy["els"]["ob-modal"]["hidden"])                  # stays open and says why
        self.assertIn("çalışan bir iş var", busy["els"]["ob-modal"]["html"])
        self.assertIn("site is scanning", busy["els"]["ob-modal"]["html"])
        self.assertNotIn("disabled", busy["els"]["ob-modal"]["html"])

    # --- draft screen: header, overall status, steps ----------------------------------------------------------------------

    def test_header_strip_host_status_time_cancel(self):
        names = {"running": "Çalışıyor", "ready": "Hazır", "needs_input": "Soru bekliyor", "failed": "Başarısız", "cancelled": "İptal", "saved": "Kaydedildi"}
        got = self.run_ui(*[{"draft": ui_draft(s)} for s in names])
        for (status, label), g in zip(names.items(), got):
            if status == "running":
                self.assertNotIn(label, g["els"]["ob-st"]["html"])   # the blue status strip says it; no second badge
            else:
                self.assertIn(label, g["els"]["ob-st"]["html"], status)
            self.assertEqual(g["els"]["ob-host"]["text"], "x.example")
            self.assertEqual(g["els"]["ob-cancel"]["hidden"], status != "running", status)   # only a running job can be cancelled
        self.assertEqual(got[1]["els"]["ob-time"]["text"], "1 dk 0 sn")
        self.assertIn('data-ob="back"', got[0]["els"]["ob-root"]["html"])
        self.assertIn("‹ Liste", got[0]["els"]["ob-root"]["html"])
        clicked = self.run_ui({"draft": ui_draft("running"), "actions": [{"click": {"act": "cancel"}}]})[0]
        self.assertEqual(clicked["posts"][0]["url"], "/api/ops/onboard/%s/cancel" % DRAFT_ID)
        back = self.run_ui({"draft": ui_draft(), "actions": [{"click": {"act": "back"}}]})[0]["els"]
        self.assertFalse(back["ob-listv"]["hidden"])
        self.assertTrue(back["ob-detv"]["hidden"])

    def test_notes_for_failed_cancelled_and_waiting_drafts(self):
        failed, cancelled, waiting = self.run_ui(
            {"draft": ui_draft("failed", error="pi çıkış kodu 2 <boom>")},
            {"draft": ui_draft("cancelled")},
            {"draft": ui_draft("needs_input"), "events": [{"kind": "say", "text": "Liste sayfası hangisi?", "t": "2026-01-01T00:00:01Z"}]})
        self.assertIn("pi çıkış kodu 2 &lt;boom&gt;", failed["els"]["ob-note"]["html"])
        self.assertIn("iptal edildi", cancelled["els"]["ob-note"]["html"])
        self.assertIn("Liste sayfası hangisi?", waiting["els"]["ob-note"]["html"])
        self.assertIn("yanıtını", waiting["els"]["ob-note"]["html"])

    def test_overall_headline_colours(self):
        cases = [("ok", "Tüm adımlar çalışıyor", "ok"), ("warn", "Dikkat: bazı adımlar eksik", "warn"), ("fail", "Sorun 3. adımda: bölüm listesi alınamadı", "bad"),
                 ("running", "Ajan çalışıyor", "run"), ("idle", "Henüz sonuç yok.", "")]
        got = self.run_ui(*[{"draft": ui_draft(pipeline=pipe(["pending"] * 6, state=s, headline=h))} for s, h, _ in cases])
        for (state, headline, cls), g in zip(cases, got):
            html = g["els"]["ob-headline"]["html"]
            self.assertIn(esc(headline), html, state)
            self.assertIn('class="obov %s"' % cls, html, state)

    def test_steps_show_state_title_question_summary_numbers_and_the_problem_box(self):
        states = ["ok", "ok", "fail", "pending", "pending", "skipped"]
        draft = ui_draft(pipeline=pipe(states, state="fail", headline="Sorun 3. adımda: bölüm listesi alınamadı", problem_step="info"))
        got = self.draft_ui(draft)["els"]["ob-steps"]["html"]
        self.assertEqual(self.step_states(got), states)
        for sid, title, question in PIPE_STEPS:
            self.assertIn('data-step="%s"' % sid, got)
            self.assertIn(esc(title), got)
            self.assertIn(esc(question), got)
        for icon in ("✓", "✗", "⏳", "–"):
            self.assertIn(icon, got, icon)
        self.assertIn("home özeti &amp; sayılar &lt;b&gt;x&lt;/b&gt;", got)              # escaped
        self.assertNotIn("<b>x</b>", got)
        self.assertEqual(got.count('class="obchip"'), 4)                                     # numbers: 2 ok steps x 2 chips
        self.assertIn("<b>4</b> Bölüm", got)
        # only the failing step has the problem box, and it is the highlighted one
        self.assertEqual(got.count("obprob"), 1)
        self.assertIn("info sorunu: yaml&#39;a series_page ekle &lt;i&gt;(ipucu)&lt;/i&gt;", got)
        self.assertEqual(got.count("isprob"), 1)
        self.assertRegex(got, r'class="obstep st-fail isprob" data-step="info"')
        self.assertEqual(got.count("Sorun bu adımda"), 1)
        self.assertNotIn('obprob warn', got)
        self.assertIn('obprob bad', got)

    def test_a_warning_step_has_a_warn_problem_box_and_is_the_highlighted_one_when_it_is_the_first_problem(self):
        draft = ui_draft(pipeline=pipe(["warn", "ok", "ok", "ok", "ok", "skipped"], state="warn", headline="Dikkat: 1. adım", problem_step="home"))
        got = self.draft_ui(draft)["els"]["ob-steps"]["html"]
        self.assertEqual(self.step_states(got), ["warn", "ok", "ok", "ok", "ok", "skipped"])
        self.assertIn("obprob warn", got)
        self.assertRegex(got, r'class="obstep st-warn isprob" data-step="home"')
        self.assertEqual(got.count("isprob"), 1)

    def test_a_running_step_pulses_and_all_pending_is_waiting(self):
        got = self.draft_ui(ui_draft("running", pipeline=pipe(["ok", "running", "pending", "pending", "pending", "pending"], state="running", headline="Ajan çalışıyor")))
        html = got["els"]["ob-steps"]["html"]
        self.assertEqual(self.step_states(html), ["ok", "running", "pending", "pending", "pending", "pending"])
        self.assertIn("obsi pulse", html)
        self.assertIn("◔", html)
        self.assertEqual(html.count("obprob"), 0)

    def test_step_details_are_closed_by_default_and_toggle_per_step(self):
        draft = ui_draft(pipeline=pipe(["ok", "ok", "fail", "pending", "pending", "skipped"], state="fail", headline="x", problem_step="info"))
        got = self.run_ui({"draft": draft},
                          {"draft": draft, "actions": [{"click": {"act": "tog", "step": "home"}}]},
                          {"draft": draft, "actions": [{"click": {"act": "tog", "step": "home"}}, {"click": {"act": "tog", "step": "home"}}]},
                          {"draft": {**draft, "status": "running"}, "actions": [
                              {"click": {"act": "tog", "step": "home"}},
                              {"server": {"draft": {"pipeline": pipe(["ok", "ok", "running", "pending", "pending", "pending"], state="running", headline="Ajan çalışıyor")}}},
                              {"tick": 2}]})
        closed = got[0]["els"]["ob-steps"]["html"]
        self.assertNotIn("<dl", closed)
        self.assertEqual(closed.count('aria-expanded="false"'), 4)    # home, links, info and search have details; pending ones none
        self.assertIn('data-ob="tog" data-step="home"', closed)
        self.assertNotIn("12 öğe", closed)
        opened = got[1]["els"]["ob-steps"]["html"]
        self.assertEqual(opened.count("<dl"), 1)
        self.assertIn("<dt>Trendler</dt><dd>12 öğe</dd>", opened)
        self.assertIn("<dt>Yol &lt;u&gt;</dt><dd>/trend</dd>", opened)   # escaped
        self.assertEqual(opened.count('aria-expanded="true"'), 1)
        self.assertNotIn("<dl", got[2]["els"]["ob-steps"]["html"])        # the second click closes it
        again = got[3]["els"]["ob-steps"]["html"]                           # the poll brought a new pipeline: redrawn, the open step stays open
        self.assertEqual(self.step_states(again), ["ok", "ok", "running", "pending", "pending", "pending"])
        self.assertEqual(again.count("<dl"), 1)
        self.assertIn("Ajan çalışıyor", got[3]["els"]["ob-headline"]["html"])

    def test_the_app_preview_section_is_gone_from_the_page(self):
        got = self.draft_ui()["els"]
        root = got["ob-root"]["html"]
        for gone in ("Uygulamada ne görünecek", 'id="ob-appw"', 'id="ob-app"', "Slider ve trend"):
            self.assertNotIn(gone, root, gone)
        self.assertNotIn("ob-app", got)       # no element of it is drawn any more
        self.assertIn("Alınacak:", self.draft_ui(actions=[{"click": {"act": "save"}}])["els"]["ob-sv-panel"]["html"])   # the confirmation still sums up the pipeline totals

    def test_an_old_draft_without_pipeline_says_so_in_one_line(self):
        old = ui_draft()
        del old["pipeline"]
        got = self.draft_ui(old)["els"]
        self.assertIn("İlerleme bilgisi yok (eski taslak)", got["ob-steps"]["html"])
        self.assertEqual(self.step_states(got["ob-steps"]["html"]), [])
        self.assertEqual(got["ob-headline"]["html"], "")
        self.assertIn("playback: video", got["ob-yaml"]["html"])         # the rest of the page still works
        self.assertFalse(got["ob-savebtn"]["disabled"])

    def test_the_real_pipeline_builder_output_is_rendered_field_by_field(self):
        from app.scraper import onboard_pipeline as opl
        report = {"passed": False, "criteria": {"valid_count": {"value": 12, "min": 3, "ok": True}, "playable_ratio": {"value": 0.0, "min": 0.67, "ok": False}},
                  "errors": [], "warnings": [], "list": {"count": 12, "valid_count": 12}, "normalize": {"total": 12, "ok": 12, "types": {"movie": 0, "series": 12}}}
        drafts = [ui_draft("ready", report=report), ui_draft("running", report=None), ui_draft("failed", report=None, error="x")]
        for d in drafts:
            d["pipeline"] = opl.for_draft(d)
        got = self.run_ui(*[{"draft": d} for d in drafts])
        for d, g in zip(drafts, got):
            p = d["pipeline"]
            self.assertEqual(len(p["steps"]), 6)
            html = g["els"]["ob-steps"]["html"]
            self.assertEqual(self.step_states(html), [s["state"] for s in p["steps"]])
            for s in p["steps"]:
                for field in ("title", "question", "summary", "problem"):
                    if s.get(field):
                        self.assertIn(esc(s[field]), html, (s["id"], field))
                for n in s.get("numbers") or []:
                    self.assertIn(esc(n["value"]), html)
            self.assertIn(esc(p["overall"]["headline"]), g["els"]["ob-headline"]["html"])

    # --- log | yaml side by side, chat under the log ---------------------------------------------------------------------

    def test_log_and_yaml_are_side_by_side_with_the_chat_right_under_the_log_and_the_save_strip_on_top(self):
        root = self.run_ui({"hash": ""})[0]["els"]["ob-root"]["html"]
        pair = root.index('class="obpair"')
        order = [root.index(x) for x in ("Ajan günlüğü", 'id="ob-log"', 'id="ob-fb"', 'id="ob-send"', 'id="ob-yaml"')]
        self.assertEqual(order, sorted(order))                     # log, then the chat box, then (right column) the yaml
        self.assertGreater(order[0], pair)
        self.assertLess(root.index('id="ob-send"'), root.index('id="ob-yaml"'))
        self.assertLess(root.index('id="ob-savebtn"'), root.index('id="ob-steps"'))     # the one Kaydet is above the progress
        self.assertLess(root.index('id="ob-steps"'), pair)          # progress first, log + yaml below it
        self.assertLess(root.index('id="ob-headline"'), root.index('id="ob-steps"'))
        self.assertIn("Günlüğü kopyala", root)
        self.assertIn("<textarea", root)
        css = (ADMIN_DIR / "style.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.obpair\{display:grid;grid-template-columns:minmax\(0,1fr\) minmax\(0,1fr\)")
        self.assertRegex(css, r"@media \(max-width:1100px\)\{[\s\S]*?\.obpair\{grid-template-columns:1fr")           # stacked on a narrow screen
        self.assertRegex(css, r"\.obsteps\{display:grid;grid-template-columns:repeat\(6,")                  # steps: horizontal on a wide screen
        self.assertRegex(css, r"@media \(max-width:1100px\)\{\s*\.obsteps\{grid-template-columns:1fr\}")      # ... vertical on a narrow one
        self.assertRegex(css, r"\.oblog\{[^}]*user-select:text")                                          # the log stays selectable

    def test_the_log_renders_every_kind_of_event_and_the_yaml_is_read_only(self):
        events = [{"t": "2026-01-01T00:00:01Z", "kind": "tool", "name": "fetch_page", "args_short": "url=https://x.example/"},
                  {"t": "2026-01-01T00:00:02Z", "kind": "tool_result", "name": "fetch_page", "ok": True, "summary": "200 · 41 KB"},
                  {"t": "2026-01-01T00:00:03Z", "kind": "tool_result", "name": "test_config", "ok": False, "error": "yaml hatası"},
                  {"t": "2026-01-01T00:00:04Z", "kind": "say", "text": "Listeyi buldum <b>"},
                  {"t": "2026-01-01T00:00:05Z", "kind": "user", "text": "afişler yanlış"},
                  {"t": "2026-01-01T00:00:06Z", "kind": "submit", "passed": False, "errors": 1, "warnings": 2},
                  {"t": "2026-01-01T00:00:07Z", "kind": "mystery"}]                    # no text: not shown
        g = self.draft_ui(events=events)["els"]
        kids = g["ob-log"]["children"]
        self.assertEqual([c["cls"] for c in kids], ["obe tool", "obe res ok", "obe res bad", "obe say", "obe user", "obe info warn"])
        self.assertIn("🔧", kids[0]["html"])
        self.assertIn("fetch_page", kids[0]["html"])
        self.assertIn("200 · 41 KB", kids[1]["html"])
        self.assertIn("yaml hatası", kids[2]["html"])
        self.assertIn("Listeyi buldum &lt;b&gt;", kids[3]["html"])
        self.assertIn("afişler yanlış", kids[4]["html"])
        self.assertIn("kriterler sağlanmadı · 1 hata · 2 uyarı", kids[5]["html"])
        self.assertIn("<pre", g["ob-yaml"]["html"])
        self.assertIn("playback: video", g["ob-yaml"]["html"])
        self.assertIn('data-ob="copy"', g["ob-yaml"]["html"])
        self.assertNotIn("<textarea", g["ob-yaml"]["html"])                          # read-only
        noyaml = self.draft_ui(ui_draft(yaml_text=""))["els"]["ob-yaml"]["html"]
        self.assertIn("Henüz yaml yok", noyaml)
        self.assertNotIn('data-ob="copy"', noyaml)
        self.assertIn("YAML taslağı", noyaml)

    def test_copy_buttons_put_plain_text_on_the_clipboard(self):
        long_args = "url=https://x.example/" + "a" * 400
        events = [{"t": "2026-01-01T00:00:01Z", "kind": "tool", "name": "fetch_page", "args_short": long_args},
                  {"t": "2026-01-01T00:00:02Z", "kind": "tool_result", "name": "fetch_page", "ok": True, "summary": "200 · 41 KB " + "b" * 400},
                  {"t": "2026-01-01T00:00:03Z", "kind": "say", "text": "Listeyi buldum"},
                  {"t": "2026-01-01T00:00:04Z", "kind": "user", "text": "afişler yanlış"}]
        got = self.draft_ui(events=events, actions=[{"click": {"act": "copylog"}}, {"click": {"act": "copy"}}])
        log, yml = got["copied"]
        self.assertIn("🔧 fetch_page " + long_args, log)                      # not clipped like the screen
        self.assertIn("✓ fetch_page — 200 · 41 KB " + "b" * 400, log)
        self.assertIn("Ajan: Listeyi buldum", log)
        self.assertIn("Sen: afişler yanlış", log)
        self.assertEqual(len(log.splitlines()), 4)
        self.assertNotIn("<", log)
        self.assertEqual(yml, "playback: video\nbase_url: https://x.example\n")
        empty = self.run_ui({"draft": ui_draft(), "actions": [{"click": {"act": "copylog"}}]})[0]
        self.assertEqual(empty["copied"], [])
        self.assertIn("Günlük boş", empty["toasts"][0])

    def test_polling_is_incremental_and_follows_the_job_only_while_it_runs(self):
        run = ui_draft("running", pipeline=pipe(["ok", "running", "pending", "pending", "pending", "pending"], state="running", headline="Ajan çalışıyor"))
        events = [{"kind": "tool", "name": "fetch_page"}, {"kind": "tool_result", "name": "fetch_page", "ok": True, "summary": "ok"}, {"kind": "say", "text": "bir"}]
        done = ui_draft("ready")
        got = self.run_ui(
            {"draft": run, "events": events, "actions": [
                {"server": {"push": [{"kind": "say", "text": "iki"}, {"kind": "say", "text": "üç"}]}}, {"tick": 2},
                {"server": {"draft": {"status": "ready", "pipeline": done["pipeline"]}, "push": [{"kind": "submit", "passed": True}]}}, {"tick": 2},
                {"tick": 4}]},
            {"draft": run, "events": events, "actions": [{"hidden": True}, {"server": {"push": [{"kind": "say", "text": "iki"}]}}, {"tick": 6}]},
            {"draft": done, "events": events, "actions": [{"tick": 6}]})
        a, hidden, finished = got
        gets = [u for u in a["gets"] if "events_after" in u]
        self.assertEqual(gets, ["/api/ops/onboard/%s?events_after=%s" % (DRAFT_ID, n) for n in (0, 3, 5)])   # only the new events are asked for
        self.assertEqual(len(a["els"]["ob-log"]["children"]), 6)
        self.assertEqual(self.step_states(a["els"]["ob-steps"]["html"]), ["ok"] * 5 + ["skipped"])          # the steps follow the live job
        self.assertIn("Tüm adımlar çalışıyor", a["els"]["ob-headline"]["html"])
        self.assertEqual(len([u for u in hidden["gets"] if "events_after" in u]), 1)      # hidden tab: no polling
        self.assertEqual(len([u for u in finished["gets"] if "events_after" in u]), 1)    # not running: no polling

    def test_the_log_follows_the_bottom_only_when_the_user_is_there(self):
        running = ui_draft("running")
        more = {"server": {"push": [{"kind": "say", "text": "yeni"}]}}
        at_bottom, reading = self.run_ui(
            {"draft": running, "events": [{"kind": "say", "text": "a"}], "actions": [{"prop": {"id": "ob-log", "props": {"scrollHeight": 1000, "clientHeight": 300, "scrollTop": 700}}}, more, {"tick": 2}]},
            {"draft": running, "events": [{"kind": "say", "text": "a"}], "actions": [{"prop": {"id": "ob-log", "props": {"scrollHeight": 1000, "clientHeight": 300, "scrollTop": 100}}}, more, {"tick": 2}]})
        self.assertEqual(len(at_bottom["els"]["ob-log"]["children"]), 2)
        self.assertEqual(at_bottom["els"]["ob-log"]["scrollTop"], 1000)      # was at the bottom: follows the new event
        self.assertEqual(len(reading["els"]["ob-log"]["children"]), 2)
        self.assertEqual(reading["els"]["ob-log"]["scrollTop"], 100)         # the reader is left where they are

    # --- chat under the log -----------------------------------------------------------------------------------------------

    def test_chat_sends_the_message_and_it_shows_in_the_log_as_the_user(self):
        got = self.draft_ui(actions=[{"set": {"id": "ob-fb", "value": "  afişler yanlış alanı alıyor  "}}, {"click": {"act": "send"}}])
        self.assertEqual(got["posts"], [{"url": "/api/ops/onboard/%s/message" % DRAFT_ID, "method": "POST", "body": {"text": "afişler yanlış alanı alıyor"}}])
        self.assertEqual(got["els"]["ob-fb"]["value"], "")
        kids = got["els"]["ob-log"]["children"]
        self.assertEqual(kids[-1]["cls"], "obe user")
        self.assertIn("afişler yanlış alanı alıyor", kids[-1]["html"])
        self.assertEqual(got["status"], "running")
        self.assertTrue(got["els"]["ob-fb"]["disabled"])                          # the job runs again: the box is off
        self.assertTrue(got["els"]["ob-send"]["disabled"])
        self.assertFalse(got["els"]["ob-cancel"]["hidden"])

    def test_enter_sends_shift_enter_makes_a_new_line_and_an_empty_box_does_not_send(self):
        enter, shift, empty, running = self.run_ui(
            {"draft": ui_draft(), "actions": [{"set": {"id": "ob-fb", "value": "merhaba"}}, {"key": {"key": "Enter"}}]},
            {"draft": ui_draft(), "actions": [{"set": {"id": "ob-fb", "value": "merhaba"}}, {"key": {"key": "Enter", "shift": True}}]},
            {"draft": ui_draft(), "actions": [{"set": {"id": "ob-fb", "value": "   "}}, {"key": {"key": "Enter"}}, {"click": {"act": "send"}}]},
            {"draft": ui_draft("running"), "actions": [{"set": {"id": "ob-fb", "value": "merhaba"}}, {"key": {"key": "Enter"}}, {"click": {"act": "send"}}]})
        self.assertEqual([p["body"] for p in enter["posts"]], [{"text": "merhaba"}])
        self.assertEqual(enter["prevented"], 1)
        self.assertEqual(shift["posts"], [])
        self.assertEqual(shift["prevented"], 0)                                   # the browser inserts the new line
        self.assertEqual(empty["posts"], [])
        self.assertIn("Önce bir mesaj yaz", empty["toasts"][0])
        self.assertEqual(running["posts"], [])                                    # a running job: nothing is sent
        other = self.run_ui({"draft": ui_draft(), "actions": [{"set": {"id": "ob-fb", "value": "x"}}, {"key": {"key": "Enter", "id": "ob-sid"}}]})[0]
        self.assertEqual(other["posts"], [])                                      # Enter elsewhere is not the chat

    def test_chat_is_off_while_running_and_on_otherwise(self):
        got = self.run_ui(*[{"draft": ui_draft(s)} for s in ("running", "ready", "needs_input", "failed", "cancelled", "saved")])
        self.assertEqual([g["els"]["ob-fb"]["disabled"] for g in got], [True, False, False, False, False, False])
        self.assertEqual([g["els"]["ob-send"]["disabled"] for g in got], [True, False, False, False, False, False])
        self.assertIn("çalışırken", got[0]["els"]["ob-fbh"]["text"])
        self.assertEqual(got[1]["els"]["ob-fbh"]["text"], "")

    # --- the pinned save strip + the confirmation -------------------------------------------------------------------------

    def test_the_save_strip_is_pinned_on_top_and_the_old_bottom_card_is_gone(self):
        root = self.run_ui({"hash": ""})[0]["els"]["ob-root"]["html"]
        top, sv = root.index('class="obtop"'), root.index('id="ob-savebtn"')
        self.assertLess(root.index('id="ob-detv"'), top)
        self.assertLess(top, sv)
        for later in ('id="ob-headline"', 'id="ob-steps"', 'id="ob-log"', 'id="ob-yaml"', 'id="ob-send"'):
            self.assertLess(sv, root.index(later), later)                  # the one Kaydet is above everything else of the draft
        for needle in ('id="ob-sid"', 'id="ob-dn"', 'id="ob-sv-panel"', "Site kimliği", "Görünen ad"):
            self.assertIn(needle, root, needle)
        self.assertEqual(root.count('id="ob-savebtn"'), 1)
        for gone in ("Siteyi kaydet", 'class="card hidden obsave"', 'id="ob-save"', "ob-force", 'id="ob-sv-form"'):
            self.assertNotIn(gone, root, gone)
        css = (ADMIN_DIR / "style.css").read_text(encoding="utf-8")
        self.assertRegex(css, r"\.obtop\{position:sticky;top:0")
        self.assertNotRegex(css, r"\.obsave\b")

    def test_the_save_button_is_visible_in_every_state_and_says_why_it_is_off(self):
        names = ("running", "ready", "needs_input", "failed", "cancelled", "saved")
        got = self.run_ui(*[{"draft": ui_draft(s)} for s in names])
        self.assertEqual([g["els"]["ob-savebtn"]["disabled"] for g in got], [True, False, True, True, True, True])
        why = [g["els"]["ob-savebtn"]["title"] for g in got]
        for text, needle in zip(why, ("ajan çalışıyor", "", "yanıtını bekliyor", "başarısız", "iptal", "kaydedildi")):
            self.assertIn(needle, text)
        self.assertEqual(why[1], "")
        self.assertEqual([g["els"]["ob-savebtn"]["text"] for g in got], ["Kaydet"] * 5 + ["Kaydedildi"])
        self.assertEqual(got[1]["els"]["ob-sid"]["value"], "x_example")                    # the suggestion
        self.assertFalse(got[1]["els"]["ob-sid"]["readOnly"])
        self.assertEqual(got[1]["els"]["ob-sv-info"]["html"], "")
        saved = got[5]["els"]
        self.assertIn("Kaydedildi (v1)", saved["ob-sv-info"]["html"])
        self.assertIn('data-ob="back"', saved["ob-sv-info"]["html"])
        self.assertIn("Site listesine dön", saved["ob-sv-info"]["html"])
        self.assertTrue(saved["ob-sid"]["readOnly"])
        self.assertTrue(saved["ob-dn"]["disabled"])
        noyaml = self.draft_ui(ui_draft(yaml_text=""))["els"]
        self.assertTrue(noyaml["ob-savebtn"]["disabled"])
        self.assertIn("yaml", noyaml["ob-savebtn"]["title"])

    def test_a_bad_site_id_turns_the_button_off_and_says_so(self):
        got = self.draft_ui(actions=[{"set": {"id": "ob-sid", "value": "Kötü Kimlik"}}, {"click": {"act": "save", "disabled": True}}])
        self.assertTrue(got["els"]["ob-savebtn"]["disabled"])
        self.assertIn("Küçük harfle başlamalı", got["els"]["ob-sid-err"]["text"])
        self.assertIn("geçerli bir site kimliği", got["els"]["ob-savebtn"]["title"])
        self.assertTrue(got["els"]["ob-sv-panel"]["hidden"])
        self.assertEqual(got["posts"], [])
        fixed = self.draft_ui(actions=[{"set": {"id": "ob-sid", "value": "Kötü Kimlik"}}, {"set": {"id": "ob-sid", "value": "ornek_site"}}])
        self.assertFalse(fixed["els"]["ob-savebtn"]["disabled"])
        self.assertEqual(fixed["els"]["ob-sid-err"]["text"], "")

    def test_save_opens_a_compact_confirmation_with_a_summary_and_two_options(self):
        got = self.draft_ui(actions=[{"click": {"act": "save"}}])
        panel = got["els"]["ob-sv-panel"]
        self.assertFalse(panel["hidden"])
        self.assertEqual(got["posts"], [])                                  # the click only opens the confirmation
        for needle in ("Kaydetmeden önce", "Alınacak: 458 dizi · 0 film · 120 bölüm", "oynatılabilir örnek: 2/3", "Kaydedince siteyi hemen tara", "Otomatik taramayı aç",
                       "Onayla ve kaydet", 'data-ob="confirm"', 'data-ob="svclose"'):
            self.assertIn(needle, panel["html"], needle)
        self.assertIn('id="ob-scan" checked', panel["html"])                  # scan right away: on by default
        self.assertNotIn('id="ob-en" checked', panel["html"])                 # automatic scanning: off by default
        for absent in ("Bazı adımlar tamam değil", "Yine de kaydet"):
            self.assertNotIn(absent, panel["html"], absent)
        closed = self.draft_ui(actions=[{"click": {"act": "save"}}, {"click": {"act": "svclose"}}])["els"]["ob-sv-panel"]
        self.assertTrue(closed["hidden"])
        self.assertEqual(closed["html"], "")

    def test_confirm_posts_the_form_with_scan_now_and_shows_the_saved_state(self):
        got = self.draft_ui(actions=[{"set": {"id": "ob-sid", "value": "ornek_site"}}, {"set": {"id": "ob-dn", "value": "Örnek Site"}},
                                     {"click": {"act": "save"}}, {"click": {"act": "confirm"}}])
        self.assertEqual(got["posts"], [{"url": "/api/ops/onboard/%s/save" % DRAFT_ID, "method": "POST",
                                         "body": {"site_id": "ornek_site", "display_name": "Örnek Site", "scan_now": True, "enable": False}}])   # no force: all is fine
        g = got["els"]
        info = g["ob-sv-info"]["html"]
        for needle in ("Kaydedildi (v1)", "ornek_site", "siteye eklendi", "Tarama başladı", "Olay defterinde izleyebilirsin", 'data-ob="back"', "Site listesine dön", 'data-ob="goto-events"'):
            self.assertIn(needle, info, needle)
        self.assertTrue(g["ob-savebtn"]["disabled"])
        self.assertEqual(g["ob-savebtn"]["text"], "Kaydedildi")
        self.assertTrue(g["ob-sv-panel"]["hidden"])
        self.assertIn("Site eklendi: ornek_site", got["toasts"][0])
        back = self.draft_ui(actions=[{"click": {"act": "save"}}, {"click": {"act": "confirm"}}, {"click": {"act": "back"}}])["els"]
        self.assertFalse(back["ob-listv"]["hidden"])
        bad = self.draft_ui(actions=[{"set": {"id": "ob-sid", "value": "Kötü Kimlik"}}, {"click": {"act": "confirm"}}])
        self.assertEqual(bad["posts"], [])

    def test_the_two_checkboxes_decide_scan_now_and_enable_and_survive_a_redraw(self):
        got = self.draft_ui(actions=[{"click": {"act": "save"}}, {"set": {"id": "ob-scan", "checked": False}}, {"set": {"id": "ob-en", "checked": True}},
                                     {"set": {"id": "ob-sid", "value": "x_example"}}])          # the input redraws the panel from the state
        html = got["els"]["ob-sv-panel"]["html"]
        self.assertNotIn('id="ob-scan" checked', html)
        self.assertIn('id="ob-en" checked', html)
        sent = self.draft_ui(actions=[{"click": {"act": "save"}}, {"set": {"id": "ob-scan", "checked": False}}, {"set": {"id": "ob-en", "checked": True}}, {"click": {"act": "confirm"}}])
        self.assertEqual(sent["posts"][0]["body"], {"site_id": "x_example", "scan_now": False, "enable": True})
        self.assertIn("Tarama başlatılmadı", sent["els"]["ob-sv-info"]["html"])
        self.assertNotIn("goto-events", sent["els"]["ob-sv-info"]["html"])
        self.assertIn('data-ob="goto-settings"', sent["els"]["ob-sv-info"]["html"])

    def test_a_warning_failed_step_or_report_is_flagged_in_the_confirmation_and_needs_force(self):
        failed_report = ui_draft(report={"passed": False, "criteria": {"valid_count": {"value": 1, "min": 3, "ok": False}}, "errors": [], "warnings": []})
        failed_step = ui_draft(pipeline=pipe(["ok", "ok", "fail", "pending", "pending", "skipped"], state="fail",
                                             headline="Sorun 3. adımda: bölüm listesi alınamadı", problem_step="info"))
        steps_warn = ui_draft(pipeline=pipe(["warn", "ok", "ok", "ok", "ok", "skipped"], state="warn", headline="Dikkat", problem_step="home"))
        clean = ui_draft()
        save = {"click": {"act": "save"}}
        a, b, c, d = self.run_ui(*[{"draft": x, "actions": [save]} for x in (failed_report, failed_step, steps_warn, clean)])
        for g in (a, b, c):
            html = g["els"]["ob-sv-panel"]["html"]
            self.assertIn("Bazı adımlar tamam değil:", html)
            self.assertIn("Yine de kaydedebilirsin", html)
            self.assertIn("Yine de kaydet", html)
            self.assertNotIn("Onayla ve kaydet", html)
        self.assertIn("Kabul kriterleri", a["els"]["ob-sv-panel"]["html"])
        self.assertIn("valid_count", a["els"]["ob-sv-panel"]["html"])
        self.assertIn(esc("Dizi bilgisi: özet, oyuncu, sezon, bölüm"), b["els"]["ob-sv-panel"]["html"])
        self.assertIn("info sorunu: yaml&#39;a series_page ekle &lt;i&gt;(ipucu)&lt;/i&gt;", b["els"]["ob-sv-panel"]["html"])
        self.assertIn("Ana sayfa bölümleri", c["els"]["ob-sv-panel"]["html"])                  # a warning alone is flagged too
        self.assertNotIn("Bazı adımlar tamam değil", d["els"]["ob-sv-panel"]["html"])
        self.assertFalse(a["els"]["ob-savebtn"]["disabled"])                                    # the strip's button is never blocked by that: the panel asks
        forced = self.run_ui({"draft": failed_step, "actions": [save, {"click": {"act": "confirm"}}]})[0]
        self.assertEqual(forced["posts"][0]["body"]["force"], True)
        self.assertEqual(forced["posts"][0]["body"]["site_id"], "x_example")
        plain = self.run_ui({"draft": clean, "actions": [save, {"click": {"act": "confirm"}}]})[0]
        self.assertNotIn("force", plain["posts"][0]["body"])

    def test_a_server_side_criteria_refusal_turns_the_confirm_into_force(self):
        refusal = {"status": 409, "code": "not_passed", "message": "the draft does not meet the criteria (search_ok)"}
        save, confirm = {"click": {"act": "save"}}, {"click": {"act": "confirm"}}
        late = self.draft_ui(actions=[save, confirm], save_fail_once=refusal)
        html = late["els"]["ob-sv-panel"]["html"]
        self.assertEqual(len(late["posts"]), 1)
        self.assertIn("the draft does not meet the criteria (search_ok)", html)
        self.assertIn("Yine de kaydet", html)
        self.assertFalse(late["els"]["ob-sv-panel"]["hidden"])                               # still open, to answer
        self.assertIn("Kaydedilemedi", late["toasts"][0])
        retry = self.draft_ui(actions=[save, confirm, confirm], save_fail_once=refusal)
        self.assertEqual([p["body"].get("force") for p in retry["posts"]], [None, True])
        self.assertIn("Kaydedildi (v1)", retry["els"]["ob-sv-info"]["html"])
        other = self.draft_ui(actions=[save, confirm], save_error="site 'x_example' already exists")
        self.assertIn("already exists", other["els"]["ob-sv-panel"]["html"])
        self.assertFalse(other["els"]["ob-savebtn"]["disabled"])

    def test_an_edit_draft_locks_the_site_id_and_saves_a_new_version(self):
        draft = ui_draft(mode="edit", edit_site_id="x_example", site_id_suggestion="x_example", hint="afişler yanlış")
        sc = {"draft": draft, "sites": [site_row()], "save_version": 4}
        got = self.run_ui(sc)[0]["els"]
        self.assertTrue(got["ob-sid"]["readOnly"])
        self.assertEqual(got["ob-sid"]["value"], "x_example")
        self.assertEqual(got["ob-savebtn"]["text"], "Yeni sürüm olarak kaydet (v4)")        # the site is at v3
        self.assertFalse(got["ob-savebtn"]["disabled"])
        self.assertEqual(got["ob-dn"]["value"], "X Example")                                  # the current name
        self.assertIn("Düzenleme: x_example", got["ob-st"]["html"])
        save, confirm = {"click": {"act": "save"}}, {"click": {"act": "confirm"}}
        panel = self.run_ui({**sc, "actions": [save]})[0]["els"]["ob-sv-panel"]["html"]
        self.assertIn("Onayla ve yeni sürüm kaydet", panel)
        done = self.run_ui({**sc, "actions": [save, confirm]})[0]
        self.assertEqual(done["posts"][0]["body"], {"site_id": "x_example", "display_name": "X Example", "scan_now": True})   # no "enable": an open auto-scan stays open
        info = done["els"]["ob-sv-info"]["html"]
        for needle in ("Kaydedildi (v4)", "yeni sürüm olarak kaydedildi", "Tarama başladı"):
            self.assertIn(needle, info, needle)
        enabled = self.run_ui({**sc, "actions": [save, {"set": {"id": "ob-en", "checked": True}}, confirm]})[0]
        self.assertEqual(enabled["posts"][0]["body"]["enable"], True)
        unknown = self.run_ui({**sc, "sites": []})[0]["els"]                                   # the list has no version: no number in the label
        self.assertEqual(unknown["ob-savebtn"]["text"], "Yeni sürüm olarak kaydet")
        self.assertEqual(unknown["ob-sid"]["value"], "x_example")
        self.assertIn("/api/ops/sites/manage", self.run_ui({**sc, "sites": []})[0]["gets"])   # asked lazily when the draft is opened directly

    def test_the_provider_recipes_that_will_be_added_are_named_in_the_save_card(self):
        draft = ui_draft()
        draft["report"]["provider_recipes"] = [{"name": "demo_player", "valid": True, "hosts": ["player\\.example"]},
                                               {"name": "broken_player", "valid": False, "hosts": []}]
        raw = ui_draft()
        raw["provider_recipes"] = [{"name": "raw_player", "yaml": "name: raw_player"}]
        saved = ui_draft("saved", saved_site_id="demo")
        saved["report"]["provider_recipes"] = draft["report"]["provider_recipes"][:1]
        plain = ui_draft()
        a, b, c, d = self.run_ui({"draft": draft}, {"draft": raw}, {"draft": saved}, {"draft": plain})
        info = a["els"]["ob-sv-info"]["html"]
        self.assertIn("Şu tarifler de kütüphaneye eklenecek", info)
        for needle in ("demo_player", "broken_player", "geçersiz"):
            self.assertIn(needle, info, needle)
        self.assertIn("raw_player", b["els"]["ob-sv-info"]["html"])               # a draft without report rows: the raw list
        self.assertIn("Kütüphaneye eklenen provider tarifleri", c["els"]["ob-sv-info"]["html"])
        self.assertIn("demo_player", c["els"]["ob-sv-info"]["html"])
        self.assertNotIn("tarifler", d["els"]["ob-sv-info"]["html"])

    # --- what is gone -----------------------------------------------------------------------------------------------------

    def test_the_technical_sections_are_gone(self):
        report = {"passed": False, "criteria": {"valid_count": {"value": 2, "min": 3, "ok": False}, "playable_ratio": {"value": 0.3, "min": 0.67, "ok": False}},
                  "errors": ["x"], "warnings": ["y"], "notes": "ajan notu", "list": {"count": 2, "valid_count": 2, "field_fill": {"title": 1.0}, "samples": [{"title": "A"}]},
                  "normalize": {"total": 2, "ok": 2, "samples": [{"title": "A"}]}, "detail": {"fields": {"title": "A"}},
                  "collections": [{"id": "trending_x", "role": "trending", "status": "ok", "count": 12, "valid_count": 11, "samples": [{"title": "B"}]}],
                  "series": {"checked": 1, "with_episodes": 0, "samples": [{"key": "a", "series_url": "https://x.example/a", "episodes": 0}]},
                  "playable": {"checked": 1, "resolved": 0, "samples": [{"key": "a", "kind": "episode", "locator": "https://x.example/a", "ok": False}]},
                  "search": {"query": "q", "count": 0}, "resolver_test": {"status": "no_stream", "candidates": [{"url": "u"}], "resolved": []},
                  "provider_recipes": []}
        draft = ui_draft(report=report, yaml_text="playback: trailer\n")
        got = self.draft_ui(draft, events=[{"kind": "tool_result", "name": "test_resolvers", "ok": True, "summary": "status=no_stream, candidate_count=2"}])
        everything = "".join(e["html"] for e in got["els"].values())
        for gone in ("Kabul kriterleri", "Örnek öğeler", "Alan doluluğu", "Normalize", "Detay sayfası alanları", "Resolver denemesi", "Ajan notları",
                     "Resolver tipleri", "Oynatıcıyı çöz", "data-ob=\"solve\"", "Koleksiyonlar", "Dizi envanteri", "Oynatılabilirlik", "Provider tarifleri",
                     "Provider kütüphanesi", "Sonuç raporu", "data-rt=", "resapply", "Geçmiş taslaklar", "obtbl"):
            self.assertNotIn(gone, everything, gone)
        self.assertNotIn("Oynatıcıyı çöz", ADMIN_JS.read_text(encoding="utf-8"))
        self.assertNotIn("ajan notu", everything)

    def test_every_ob_class_in_the_stylesheet_is_used_by_the_page(self):
        css = (ADMIN_DIR / "style.css").read_text(encoding="utf-8")
        page = ADMIN_JS.read_text(encoding="utf-8") + (ADMIN_DIR / "index.html").read_text(encoding="utf-8")
        used = set(re.findall(r"\.(ob[a-z0-9]*)\b", css))
        self.assertGreater(len(used), 30)
        unused = sorted(c for c in used if c not in page)
        self.assertEqual(unused, [], "stylesheet rules for classes nothing uses: %s" % unused)

    def test_all_admin_scripts_are_valid_javascript(self):
        for name in ("app.js", "library.js", "settings.js", "onboard.js"):
            proc = subprocess.run(["node", "--check", str(ADMIN_DIR / name)], capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, name + ": " + proc.stderr)


class PlayerFetch:
    """The transport of a recipe: canned player pages by URL, over the http transport (``fetch_impersonated``: Chrome TLS
    fingerprint + Referer) and the browser one. ``REFERER_ONLY`` pages answer 404 unless that Referer is sent."""

    def __init__(self, pages):
        self.pages, self.log = pages, []

    def impersonated_session(self, impersonate="chrome"):
        raise AssertionError("the examples do not use warm_session")

    def fetch_impersonated(self, url, *, headers=None, cookies=None, timeout=12.0, max_bytes=3_000_000, max_redirects=3,
                           allow=None, impersonate="chrome", session=None):
        self.log.append(("http", url, headers or {}))
        wanted = REFERER_ONLY.get(url)
        if wanted is not None and (headers or {}).get("Referer") != wanted:
            raise FetchError("HTTP 404 for " + url, 404)
        return self.pages[url]

    def browser_page(self, cfg, url, *, wait_for=""):
        self.log.append(("browser", url, wait_for))
        return self.pages[url]

    def reachable(self, url, **kwargs):
        return True


DETAIL_URL = "https://demo.example/film/100/film-0"   # the page the players of the samples are embedded in
REFERER_URL = "https://refererplayer.example/player/oynat/428f7f69"
REFERER_ONLY = {REFERER_URL: DETAIL_URL}   # player pages that 404 without the detail page as Referer
PACKED_JS = 'var player=jwplayer("vp");player.setup({sources:[{file:"https://cdn.example/hls/p9/index.m3u8"}]});'
QUALITY_PAGE = ('<script>player.setup({sources:[{file:"https://cdn.example/q/360.mp4", label:"360p"}, '
                '{file:"https://cdn.example/q/master.m3u8", label:"auto"}, {file:"https://cdn.example/q/1080.mp4", label:"1080p"}, '
                '{file:"https://cdn.example/q/720.mp4", label:"720p"}]});</script>')
MIXED_URL = "https://mixed.example/player/m1"
MIXED_PAGES = {   # the three player formats of one site (mixed_formats_player): extension-less file hosts, escaped HLS, googlevideo
    "yandex": '<script>jwplayer().setup({file:"https://downloader.disk.yandex.ru/disk/abc?filename=v.bin"});</script>',
    "hls": '<script>jwplayer().setup({file:"https:\\/\\/video.twimg.com\\/pl\\/x.m3u8?tag=12", label:"auto"});</script>',
    "google": '<script>jwplayer().setup({file:"https://redirector.googlevideo.com/videoplayback?id=1&itag=22"});</script>',
}
PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]


def recipe_samples():
    """recipe name -> (player URL, {url: player page}, first stream URL, transport): the page each example recipe is made for."""
    packed = "<script>" + _unpack.pack(PACKED_JS, sorted(set(re.findall(r"\w+", PACKED_JS)))) + "</script>"
    b64 = base64.b64encode(b"https://cdn.example/hls/b2/master.m3u8").decode()

    def one(url, body, expected, transport="http"):
        return (url, {url: body}, expected, transport)

    return {
        "plain_url_player": one("https://plainurl.example/player/a1",
                                '<script>var p=new Playerjs({id:"pl",file:"https:\\/\\/cdn.example\\/hls\\/a1\\/master.m3u8?t=9"});</script>',
                                "https://cdn.example/hls/a1/master.m3u8?t=9"),
        "source_tag_player": one("https://sourcetag.example/embed/ep1",
                                 '<video><source src="/media/ep1/720.mp4" size="720"><source src="/media/ep1/1080.mp4" size="1080"></video>',
                                 "https://sourcetag.example/media/ep1/1080.mp4"),
        "packed_player": one("https://packed.example/player/p9", packed, "https://cdn.example/hls/p9/index.m3u8"),
        "base64_player": one("https://base64.example/player/b2", '<script>var s = atob("%s"); load(s);</script>' % b64,
                             "https://cdn.example/hls/b2/master.m3u8"),
        "nested_browser_player": ("https://nested.example/player/n1", {
            "https://nested.example/player/n1": '<html><iframe src="https://embed.example/e/xyz"></iframe></html>',
            "https://embed.example/e/xyz": '<script>sources:[{file:"https:\\/\\/cdn.example\\/hls\\/n1\\/m.m3u8"}]</script>'},
            "https://cdn.example/hls/n1/m.m3u8", "browser"),
        "json_api_player": one("https://jsonapi.example/api/player/j1",
                               '{"status": true, "sources": [{"file": "https://cdn.example/v/c3.mp4", "label": "720p"}]}',
                               "https://cdn.example/v/c3.mp4"),
        "referer_player": one(REFERER_URL, '<html><script>sources: [{file:"https://cdn.example/v.mp4?x=1&y=2"}]</script></html>',
                              "https://cdn.example/v.mp4?x=1&y=2"),
        "quality_player": one("https://qualities.example/player/q1", QUALITY_PAGE, "https://cdn.example/q/1080.mp4"),
        "mixed_formats_player": one(MIXED_URL, MIXED_PAGES["yandex"], "https://downloader.disk.yandex.ru/disk/abc?filename=v.bin"),
    }


class ProviderRecipeExamples(unittest.TestCase):
    """The recipes of ``references/examples/providers/`` are real: each one validates and, run against the sample player page it
    is made for, yields the stream the comment promises; the flags that make them work are the ones the text names."""

    def setUp(self):
        self.samples = recipe_samples()
        self.examples = example_recipes()

    def run_example(self, name, samples=None, **override):
        data = {**self.examples[name], **override}
        player, pages, _expected, _transport = (samples or self.samples)[name]
        fetcher = PlayerFetch(pages)
        provider = recipes.RecipeProvider(data, fetch_api=fetcher)
        with patch("socket.getaddrinfo", return_value=PUBLIC_DNS):
            self.assertTrue(provider.matches(player), name)
            got = provider.resolve(player, referer=DETAIL_URL)
        return got, fetcher

    def test_the_example_set_and_the_samples_are_the_same(self):
        self.assertEqual(sorted(self.examples), sorted(self.samples))
        self.assertEqual(len(self.examples), 9)

    def test_every_example_validates_and_is_named_like_its_file(self):
        for stem, data in self.examples.items():
            with self.subTest(recipe=stem):
                self.assertEqual(recipes.validate_recipe(data), [])
                self.assertEqual(data["name"], stem)
                self.assertEqual(data["version"], 1)
                self.assertTrue(data["description"].strip())
                provider = recipes.RecipeProvider(data)
                self.assertEqual(provider.catalog_entry()["kind"], "recipe")
        self.assertEqual(len({d["match"]["host_regex"] for d in self.examples.values()}), 9)   # no two examples own the same host

    def test_every_example_finds_its_stream(self):
        for name in self.examples:
            _player, _pages, expected, transport = self.samples[name]
            got, fetcher = self.run_example(name)
            self.assertIsNotNone(got, name)
            self.assertEqual((got["url"], got["provider"]), (expected, name), name)
            self.assertEqual(got["streams"][0]["url"], expected, name)
            self.assertEqual({kind for kind, _url, _extra in fetcher.log}, {transport}, name)

    def test_a_recipe_only_owns_its_own_player_urls(self):
        for name, (player, _pages, _expected, _transport) in self.samples.items():
            provider = recipes.RecipeProvider(self.examples[name])
            self.assertTrue(provider.matches(player), name)
            for other, (other_url, *_rest) in self.samples.items():
                if other != name:
                    self.assertFalse(provider.matches(other_url), (name, other))
        self.assertFalse(recipes.RecipeProvider(self.examples["source_tag_player"]).matches("https://sourcetag.example/player/ep1"))   # path

    def test_the_registry_resolves_every_example_url(self):
        for name, (player, pages, expected, _transport) in self.samples.items():
            provider = recipes.RecipeProvider(self.examples[name], fetch_api=PlayerFetch(pages))
            with patch("socket.getaddrinfo", return_value=PUBLIC_DNS):
                got = registry.resolve(player, referer=DETAIL_URL, allowed=[name], extra=[provider])
            self.assertEqual(got["url"], expected, name)

    def test_the_flags_of_the_examples_are_what_make_them_work(self):
        # without unpack the packed script hides the URL
        plain = [{k: v for k, v in rule.items() if k != "unpack"} for rule in self.examples["packed_player"]["extract"]]
        self.assertIsNone(self.run_example("packed_player", extract=plain)[0])
        # base64 text is no URL without base64: true (a bogus "URL" under the player's own path, the host tells)
        raw = [{k: v for k, v in rule.items() if k != "base64"} for rule in self.examples["base64_player"]["extract"]]
        bogus = self.run_example("base64_player", extract=raw)[0]
        self.assertTrue(bogus["url"].startswith("https://base64.example/player/"), bogus)
        # no follow hop: the first page only holds the inner iframe, and a recipe never hands it on
        self.assertIsNone(self.run_example("nested_browser_player", follow=[])[0])
        # the json example sends the embedding site as referer and the extra header
        _got, fetcher = self.run_example("json_api_player")
        self.assertEqual(fetcher.log[0][2]["Referer"], "https://demo.example/")
        self.assertEqual(fetcher.log[0][2]["X-Requested-With"], "XMLHttpRequest")
        # the referer-protected player: the detail page is sent as Referer (the default) and nothing else works
        got, fetcher = self.run_example("referer_player")
        self.assertEqual(got["url"], "https://cdn.example/v.mp4?x=1&y=2")
        self.assertEqual(fetcher.log[0][2]["Referer"], DETAIL_URL)
        self.assertIsNone(self.run_example("referer_player", referer="https://other.example/")[0])
        self.assertIsNone(self.run_example("referer_player", referer="{base}/")[0])
        self.assertEqual(self.examples["referer_player"]["fetch"], "http")
        self.assertEqual(self.examples["nested_browser_player"]["fetch"], "browser")

    def test_the_quality_and_mixed_format_examples(self):
        got, _fetcher = self.run_example("quality_player")
        self.assertEqual([(x["quality"], x["type"]) for x in got["streams"]],
                         [("1080p", "mp4"), ("720p", "mp4"), ("360p", "mp4"), ("auto", "hls")])
        # without quality_group the entries are plain "auto" streams in page order: quality_group is what ranks them
        rules = [{k: v for k, v in rule.items() if k != "quality_group"} for rule in self.examples["quality_player"]["extract"]]
        got, _fetcher = self.run_example("quality_player", extract=rules)
        self.assertEqual(got["url"], "https://cdn.example/q/360.mp4")
        # one extract rule per format: each page of the site is found by the rule that fits it
        expected = {"yandex": ("https://downloader.disk.yandex.ru/disk/abc?filename=v.bin", "mp4"),
                    "hls": ("https://video.twimg.com/pl/x.m3u8?tag=12", "hls"),
                    "google": ("https://redirector.googlevideo.com/videoplayback?id=1&itag=22", "mp4")}
        for kind, (url, media) in expected.items():
            custom = {**self.samples, "mixed_formats_player": (MIXED_URL, {MIXED_URL: MIXED_PAGES[kind]}, url, "http")}
            got, _fetcher = self.run_example("mixed_formats_player", custom)
            self.assertEqual((got["url"], got["type"]), (url, media), kind)
        # a recipe written from the first page alone (one rule, the file host) misses the HLS page
        only_hosts = self.examples["mixed_formats_player"]["extract"][1:]
        custom = {**self.samples, "mixed_formats_player": (MIXED_URL, {MIXED_URL: MIXED_PAGES["hls"]}, "", "http")}
        self.assertIsNone(self.run_example("mixed_formats_player", custom, extract=only_hosts)[0])

    def test_the_providers_reference_names_every_example_file(self):
        text = (REFS / "providers.md").read_text(encoding="utf-8")
        named = set(re.findall(r"\| `(\w+\.yaml)` \|", text))
        self.assertEqual(named, {p.name for p in RECIPE_DIR.glob("*.yaml")})
        player = " ".join((REFS / "player-authoring.md").read_text(encoding="utf-8").split())
        for name in named:
            self.assertIn("`%s`" % name, player, name)


class ProviderLibrarySkill(unittest.TestCase):
    """The skill teaches the modular split: the site yaml finds the player URL, a provider (existing or a new recipe) reads it."""

    def skill(self):
        return (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    def test_skill_section_and_references_are_in_place(self):
        text = self.skill()
        self.assertIn("\n## Player authoring\n", text)
        section = text.split("\n## Player authoring\n", 1)[1].split("\n## ", 1)[0]
        for needle in ("grep_page", "fetch_page(player_url", 'mode: "browser"', "fetch:", "test_provider", "Just a moment",
                       "m3u8|\\.mp4|file", "eval\\(function\\(p,a,c,k", "playback: video", "playback: trailer", "evidence",
                       "test_resolvers", "At most 6 rounds", "Never suggest writing code", "references/player-authoring.md",
                       "references/providers.md", "referer=<the detail page URL>", "Chrome TLS fingerprint", 'referer: "{page_url}"',
                       "warm_session", "provider_recipes", "needs code", "recipe", "match"):
            self.assertIn(needle, section, needle)
        self.assertLess(section.index("referer=<the detail page URL>"), section.index('mode: "browser"'))   # referer before the browser
        tools = text.split("## Tools", 1)[1].split("\n## ", 1)[0]
        self.assertIn("fetch_page(url, mode?, wait_for?, referer?)", tools)
        self.assertIn("`test_provider(recipe_yaml, sample_url, referer?)`", tools)
        self.assertIn("`test_resolvers(yaml_text, detail_url, page_id?, provider_recipes?)`", tools)
        self.assertIn("provider_recipes?)`", tools.split("`submit_draft(", 1)[1])
        self.assertIn("references/player-authoring.md", text.split("## Tools", 1)[0])
        self.assertIn("references/providers.md", text.split("## Tools", 1)[0])
        self.assertIn("references/examples/providers/*.yaml", text.split("## Tools", 1)[0])
        self.assertIn("Player authoring", text.split("## Player authoring", 1)[0])   # step 8 points at the section
        self.assertNotIn("code needed", text)
        self.assertNotIn("code needed", (REFS / "resolvers.md").read_text(encoding="utf-8"))

    def test_the_task_and_step_8_are_modular(self):
        text = self.skill()
        task = text.split("## Tools", 1)[0]
        for needle in ("**modular**", "provider library", "provider recipe", "You do not embed player rules in the site yaml"):
            self.assertIn(needle, " ".join(task.split()), needle)
        step8 = text.split("8. **Player step (MANDATORY, never skip it).**", 1)[1].split("\n9. ", 1)[0]
        for needle in ("`list_resolvers`", "ONLY to FIND the player URL", "providers: [<name>]", "RECIPE", "Do NOT embed a `player_page` resolver",
                       "provider_recipes?"):
            self.assertIn(needle, " ".join(step8.split()), needle)
        self.assertIn("`provider_recipes`", text.split("10. `submit_draft(", 1)[1].split("\n\n", 1)[0])
        rules = text.split("\n## Rules\n", 1)[1]
        self.assertIn("Player knowledge goes into the provider library", rules)

    def test_the_multi_page_testing_advice_is_in_the_reference_and_the_skill(self):
        reference = " ".join((REFS / "player-authoring.md").read_text(encoding="utf-8").split())   # the text is hard-wrapped
        for needle in ("Test on at least 3 different player URLs of different titles before submitting; sites often serve different "
                       "hosts or types per title", "`quality_group`", "`type` out", "`status: partial`", "varied hosts/types",
                       "one `extract` rule per", "`detail_urls`", "skipped", "`unpack: true`", "`base64: true`", "`follow",
                       "`fetch: browser`", "json_path", "x22", "x27", "Chrome TLS fingerprint", "referer=<detail", "`warm_session: true`",
                       "`test_provider`", "`status: no_match`"):
            self.assertIn(needle, reference, needle)
        skill = self.skill().split("\n## Player authoring\n", 1)[1].split("\n## ", 1)[0]
        for needle in ("detail_urls", "status: partial", "varied hosts/types", "`auto`", "at least 3 player URLs"):
            self.assertIn(needle, skill, needle)
        self.assertNotIn("`type: hls` / `mp4`", skill)   # the old advice to pin the type

    def test_the_providers_reference_covers_the_recipe_contract(self):
        text = " ".join((REFS / "providers.md").read_text(encoding="utf-8").split())
        for needle in ("`kind`", "`code`", "`recipe`", "`match`", "host_regex", "path_regex", "`re.search`", "anchor it",
                       "`test_provider(recipe_yaml, sample_url, referer)`", "`provider_recipes`", "at most 3", "needs code",
                       "Do NOT put a `player_page` resolver into the site yaml", "`list_resolvers`", "one `extract` rule per format",
                       "^[a-z][a-z0-9_]{1,31}$", "never \"vidmolly\" / \"okru\"", "`no_match`", "`no_stream`", "`invalid`"):
            self.assertIn(needle, text, needle)
        # the limits the text states are the code's
        self.assertEqual(recipes.NAME_RE.pattern, "^[a-z][a-z0-9_]{1,31}$")
        self.assertEqual(sb.MAX_PROVIDER_RECIPES, 3)
        self.assertEqual(set(recipes.CODE_NAMES), {"vidmolly", "okru"})
        for key in ("name", "description", "version", "match.host_regex", "match.path_regex", "label", "fetch", "referer", "headers",
                    "warm_session", "wait_for", "follow", "extract", "stream_headers", "verify"):   # every key of a recipe is documented
            self.assertIn("`%s`" % key, text, key)
        self.assertTrue(set(recipes.PLAYER_KEYS) | {"name", "description", "version", "label", "match"} <= set(recipes.RECIPE_KEYS))

    def test_the_resolver_catalog_steers_to_recipes(self):
        from app.scraper import resolvers
        description = next(e["description"] for e in resolvers.catalog() if e["type"] == "player_page")
        self.assertIn("provider recipe", description)


# --- the extension, run by node against a fake sandbox ----------------------------------------------------------

HARNESS = r"""
const [, , extension, scenarioJson] = process.argv;
const mod = await import(extension);
const tools = {};
const handlers = {};
mod.default({ registerTool(tool) { tools[tool.name] = tool; }, on(name, handler) { (handlers[name] ||= []).push(handler); } });
const out = [];
for (const step of JSON.parse(scenarioJson)) {
  const controller = new AbortController();
  if (step.abort_after_ms) setTimeout(() => controller.abort(), step.abort_after_ms);
  if (step.guard) {   // a tool_call event as pi sends it; the first handler answer that is not undefined wins
    let answer;
    for (const handler of handlers.tool_call || []) {
      answer = await handler({ type: "tool_call", toolCallId: "g1", toolName: step.guard.tool || "read", input: step.guard.input });
      if (answer !== undefined) break;
    }
    out.push({ ok: true, guard: answer === undefined ? null : answer });
    continue;
  }
  try {
    const result = await tools[step.tool].execute("call-1", step.params || {}, controller.signal);
    out.push({ ok: true, type: result.content[0].type, text: result.content[0].text });
  } catch (error) {
    out.push({ ok: false, error: String(error && error.message) });
  }
}
const meta = Object.fromEntries(Object.entries(tools).map(([name, t]) => [name, { label: t.label, description: t.description, parameters: t.parameters, execute: typeof t.execute }]));
console.log(JSON.stringify({ names: Object.keys(tools), handlers: Object.fromEntries(Object.entries(handlers).map(([k, v]) => [k, v.length])), meta, out }));
"""


class FakeSandbox:
    """Records every request; answers from ``replies`` (sandbox path -> (status, body text, delay seconds))."""

    def __init__(self):
        self.requests = []
        self.replies = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                path = self.path[len(PREFIX):] if self.path.startswith(PREFIX) else self.path
                outer.requests.append({"method": self.command, "path": path, "raw": raw,
                                       "headers": {k.lower(): v for k, v in self.headers.items()},
                                       "body": json.loads(raw) if raw else None})
                status, body, delay = outer.replies.get(path, (200, '{"ok": true}', 0))
                if delay:
                    time.sleep(delay)
                data = body.encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_GET = do_POST = serve

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d%s" % (self.httpd.server_address[1], PREFIX)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ExtensionUnderNode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="onboard-ext-")
        cls.harness = os.path.join(cls.tmp, "harness.mjs")
        Path(cls.harness).write_text(HARNESS, encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.fake = FakeSandbox()
        self.addCleanup(self.fake.close)

    def run_node(self, scenario, **env_overrides):
        env = {k: v for k, v in os.environ.items() if not k.startswith("DIZIFLIX_")}
        env.update({"DIZIFLIX_SANDBOX_URL": self.fake.url, "DIZIFLIX_ONBOARD_TOKEN": "tok-secret",
                    "DIZIFLIX_DRAFT_ID": "od_0123456789ab", "NODE_NO_WARNINGS": "1"})
        env.update(env_overrides)
        env = {k: v for k, v in env.items() if v is not None}
        proc = subprocess.run(["node", self.harness, EXTENSION.as_uri(), json.dumps(scenario)], env=env,
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != 0 and re.search(r"ERR_UNKNOWN_FILE_EXTENSION|Unknown file extension", proc.stderr):
            self.skipTest("this node cannot strip TypeScript types")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def sample_params(self, schema):
        out = {}
        for name, spec in schema["properties"].items():
            out[name] = (spec["enum"][0] if "enum" in spec else 7 if spec["type"] == "integer"
                         else True if spec["type"] == "boolean" else ["v_" + name] if spec["type"] == "array" else "v_" + name)
        return out

    def test_registers_the_onboarding_tools(self):
        got = self.run_node([])
        self.assertEqual(got["names"], TOOLS)
        for name, meta in got["meta"].items():
            self.assertEqual(meta["execute"], "function", name)
            self.assertGreater(len(meta["description"]), 60, name)
            self.assertTrue(meta["label"], name)
            self.assertEqual(meta["parameters"]["type"], "object", name)

    def test_edit_mode_registers_the_onboarding_tools_plus_load_site_config(self):
        got = self.run_node([], DIZIFLIX_MODE="edit")
        self.assertEqual(got["names"], EDIT + ["load_site_config"])
        self.assertEqual(set(got["names"]), set(pi_agent.EDIT_TOOLS))
        self.assertNotIn("submit_repair", got["names"])
        self.assertEqual(got["meta"]["load_site_config"]["parameters"]["required"], ["site_id"])
        self.assertIn("edit", got["meta"]["load_site_config"]["description"])
        out = self.run_node([{"tool": "load_site_config", "params": {"site_id": "demo"}},
                             {"tool": "submit_draft", "params": {"yaml_text": "a: 1", "site_id_suggestion": "demo", "notes": "n"}}],
                            DIZIFLIX_MODE="edit")["out"]
        self.assertEqual([r["ok"] for r in out], [True, True], out)
        self.assertEqual([(r["method"], r["path"]) for r in self.fake.requests], [("GET", "/site_config/demo"), ("POST", "/submit")])
        self.assertEqual(self.fake.requests[1]["body"],
                         {"draft_id": "od_0123456789ab", "yaml_text": "a: 1", "site_id_suggestion": "demo", "notes": "n"})
        for mode in ("onboard", "repair", "zzz"):   # the other modes do not get both (submit_draft + load_site_config)
            names = self.run_node([], DIZIFLIX_MODE=mode)["names"]
            self.assertFalse({"submit_draft", "load_site_config"} <= set(names), mode)

    def test_requests_match_the_sandbox_contract(self):
        meta = self.run_node([])["meta"]
        scenario = [{"tool": name, "params": self.sample_params(meta[name]["parameters"])} for name in HTTP_TOOLS]
        got = self.run_node(scenario)
        self.assertEqual([r["ok"] for r in got["out"]], [True] * len(HTTP_TOOLS), got["out"])
        for name, request in zip(HTTP_TOOLS, self.fake.requests):
            method, path, model = ENDPOINTS[name]
            self.assertEqual((request["method"], request["path"]), (method, path), name)
            self.assertEqual(request["headers"]["x-onboard-token"], "tok-secret", name)
            props = set(meta[name]["parameters"]["properties"])
            if model is None:
                self.assertIsNone(request["body"], name)
                self.assertEqual(props, set(), name)
                continue
            sent = set(request["body"])
            self.assertEqual(sent - {"draft_id"}, props, name)             # every parameter goes through, nothing else
            self.assertTrue(sent <= set(model.model_fields), "%s sends unknown fields %s" % (name, sent - set(model.model_fields)))
            required = {n for n, f in model.model_fields.items() if f.is_required()} - {"draft_id"}
            self.assertTrue(required <= set(meta[name]["parameters"].get("required", [])), "%s: required %s" % (name, required))
            self.assertTrue(set(meta[name]["parameters"].get("required", [])) <= props, name)
        submit = self.fake.requests[HTTP_TOOLS.index("submit_draft")]["body"]
        self.assertEqual(submit["draft_id"], "od_0123456789ab")

    def test_test_search_tool(self):
        meta = self.run_node([])["meta"]["test_search"]
        self.assertEqual(meta["parameters"]["required"], ["yaml_text"])
        self.assertEqual(set(meta["parameters"]["properties"]), {"yaml_text", "query", "page_id", "detail_page_id"})
        self.assertEqual(set(meta["parameters"]["properties"]), set(sb.TestSearchBody.model_fields))
        for needle in ("search:", "found_known", "normalize_ok_ratio", "samples", "ONE query", "Nothing is written", "search_ok"):
            self.assertIn(needle, meta["description"], needle)
        got = self.run_node([
            {"tool": "test_search", "params": {"yaml_text": "search: {}\n", "query": "dark", "page_id": "pg_1"}},
            {"tool": "test_search", "params": {"yaml_text": "search: {}\n", "query": "", "page_id": "", "detail_page_id": ""}}])
        self.assertEqual([r["ok"] for r in got["out"]], [True, True])
        first, second = self.fake.requests
        self.assertEqual((first["method"], first["path"], first["headers"]["x-onboard-token"]), ("POST", "/test_search", "tok-secret"))
        self.assertEqual(first["body"], {"yaml_text": "search: {}\n", "query": "dark", "page_id": "pg_1"})
        self.assertEqual(second["body"], {"yaml_text": "search: {}\n"})   # empty optional values are not sent
        self.assertNotIn("draft_id", first["body"])

    def test_test_config_has_the_collections_flag_and_outline_describes_the_new_fields(self):
        meta = self.run_node([])["meta"]
        spec = meta["test_config"]["parameters"]["properties"]["collections"]
        self.assertEqual(spec["type"], "boolean")
        self.assertNotIn("collections", meta["test_config"]["parameters"].get("required", []))
        self.assertIn("collections: true", meta["test_config"]["description"])
        for word in ("nav_links", "sections"):
            self.assertIn(word, meta["outline_page"]["description"])
        self.run_node([{"tool": "test_config", "params": {"yaml_text": "a: 1", "collections": True}},
                       {"tool": "test_config", "params": {"yaml_text": "a: 1", "collections": False}},
                       {"tool": "test_config", "params": {"yaml_text": "a: 1"}}])
        bodies = [r["body"] for r in self.fake.requests]
        self.assertIs(bodies[0]["collections"], True)
        self.assertIs(bodies[1]["collections"], False)   # an explicit false is sent, an absent flag is not
        self.assertNotIn("collections", bodies[2])

    def test_test_config_has_the_playable_flag(self):
        meta = self.run_node([])["meta"]
        spec = meta["test_config"]["parameters"]["properties"]["playable"]
        self.assertEqual(spec["type"], "boolean")
        self.assertNotIn("playable", meta["test_config"]["parameters"].get("required", []))
        for word in ("playable: true", "playable_ratio", "series_have_episode_sources"):
            self.assertIn(word, meta["test_config"]["description"])
        self.run_node([{"tool": "test_config", "params": {"yaml_text": "a: 1", "playable": True}},
                       {"tool": "test_config", "params": {"yaml_text": "a: 1"}}])
        bodies = [r["body"] for r in self.fake.requests]
        self.assertIs(bodies[0]["playable"], True)
        self.assertNotIn("playable", bodies[1])

    def test_descriptions_know_the_series_inventory(self):
        meta = self.run_node([])["meta"]
        for word in ("episode_links", "shape", "series_page"):
            self.assertIn(word, meta["outline_page"]["description"], word)
        for word in ("series_page", "series_inventory_ok", "series{checked, with_episodes"):
            self.assertIn(word, meta["test_config"]["description"], word)

    def test_test_resolvers_takes_several_pages(self):
        meta = self.run_node([])["meta"]["test_resolvers"]
        props = meta["parameters"]["properties"]
        self.assertEqual((props["detail_urls"]["type"], props["detail_urls"]["items"], props["detail_urls"]["maxItems"]),
                         ("array", {"type": "string"}, 4))
        self.assertEqual(sb.TestResolversBody.model_json_schema()["properties"]["detail_urls"]["anyOf"][0]["maxItems"], 4)
        self.assertIn("list_page_id", props)
        self.assertEqual(meta["parameters"]["required"], ["yaml_text", "detail_url"])
        for word in ("partial", "pages[]", "varied hosts/types", "detail_urls", "skipped"):
            self.assertIn(word, meta["description"], word)
        urls = ["https://x.example/a", "https://x.example/b"]
        self.run_node([{"tool": "test_resolvers", "params": {"yaml_text": "a: 1", "detail_url": "https://x.example/", "detail_urls": urls}},
                       {"tool": "test_resolvers", "params": {"yaml_text": "a: 1", "detail_url": "https://x.example/", "detail_urls": []}},
                       {"tool": "test_resolvers", "params": {"yaml_text": "a: 1", "detail_url": "https://x.example/"}}])
        bodies = [r["body"] for r in self.fake.requests]
        self.assertEqual(bodies[0]["detail_urls"], urls)
        self.assertEqual(bodies[1]["detail_urls"], [])   # an explicit [] (this page only) is sent, an absent list is not
        self.assertNotIn("detail_urls", bodies[2])

    def test_test_provider_and_the_provider_recipes_parameter(self):
        meta = self.run_node([])["meta"]
        tool = meta["test_provider"]
        self.assertEqual(set(tool["parameters"]["properties"]), {"recipe_yaml", "sample_url", "referer"})
        self.assertEqual(tool["parameters"]["required"], ["recipe_yaml", "sample_url"])
        self.assertEqual(set(tool["parameters"]["properties"]), set(sb.TestProviderBody.model_fields))
        for word in ("matched", "no_match", "match", "referer", "SEVERAL", "Nothing is written", "provider_recipes"):
            self.assertIn(word, tool["description"], word)
        for name in ("test_config", "test_resolvers", "submit_draft"):
            spec = meta[name]["parameters"]["properties"]["provider_recipes"]
            self.assertEqual((spec["type"], spec["maxItems"], spec["items"]["required"]), ("array", 3, ["name", "yaml"]), name)
            self.assertEqual(set(spec["items"]["properties"]), set(sb.RecipeBody.model_fields), name)
            self.assertNotIn("provider_recipes", meta[name]["parameters"]["required"], name)
        for body in (sb.TestConfigBody, sb.TestResolversBody, sb.SubmitBody):
            self.assertIn("provider_recipes", body.model_fields)
        for word in ("kind", "recipe", "code"):
            self.assertIn(word, meta["list_resolvers"]["description"], word)
        items = [{"name": "demo_player", "yaml": "name: demo_player\n"}]
        got = self.run_node([
            {"tool": "test_config", "params": {"yaml_text": "a: 1", "playable": True, "provider_recipes": items}},
            {"tool": "test_resolvers", "params": {"yaml_text": "a: 1", "detail_url": "https://x.example/", "provider_recipes": items}},
            {"tool": "test_provider", "params": {"recipe_yaml": "name: demo_player\n", "sample_url": "https://player.example/p/1",
                                                 "referer": "https://x.example/film/1"}},
            {"tool": "test_provider", "params": {"recipe_yaml": "name: demo_player\n", "sample_url": "https://player.example/p/1", "referer": ""}},
            {"tool": "submit_draft", "params": {"yaml_text": "a: 1", "site_id_suggestion": "x1", "provider_recipes": items}},
            {"tool": "submit_draft", "params": {"yaml_text": "a: 1", "site_id_suggestion": "x1"}}])
        self.assertEqual([r["ok"] for r in got["out"]], [True] * 6, got["out"])
        bodies = [r["body"] for r in self.fake.requests]
        self.assertEqual(bodies[0]["provider_recipes"], items)
        self.assertEqual(bodies[1]["provider_recipes"], items)
        self.assertEqual(self.fake.requests[2]["path"], "/test_provider")
        self.assertEqual(bodies[2], {"recipe_yaml": "name: demo_player\n", "sample_url": "https://player.example/p/1",
                                     "referer": "https://x.example/film/1"})
        self.assertNotIn("referer", bodies[3])   # an empty optional value is not sent
        self.assertEqual(bodies[4]["provider_recipes"], items)
        self.assertEqual(bodies[4]["draft_id"], "od_0123456789ab")
        self.assertNotIn("provider_recipes", bodies[5])   # absent = the draft keeps the recipes it has

    def test_submit_ignores_a_draft_id_from_the_model(self):
        got = self.run_node([{"tool": "submit_draft", "params": {"yaml_text": "a: 1", "site_id_suggestion": "x1",
                                                                   "draft_id": "od_ffffffffffff"}}])
        self.assertTrue(got["out"][0]["ok"])
        self.assertEqual(self.fake.requests[0]["body"]["draft_id"], "od_0123456789ab")

    def test_optional_empty_values_are_not_sent(self):
        self.run_node([{"tool": "fetch_page", "params": {"url": "https://x.example/", "mode": "", "wait_for": None}}])
        self.assertEqual(self.fake.requests[0]["body"], {"url": "https://x.example/"})

    def test_fetch_page_has_the_referer_parameter_and_the_chrome_mode_of_the_sandbox(self):
        meta = self.run_node([])["meta"]["fetch_page"]
        props = meta["parameters"]["properties"]
        self.assertEqual(set(props["mode"]["enum"]), set(sb.FetchBody.model_json_schema()["properties"]["mode"]["enum"]))
        self.assertIn("chrome", props["mode"]["enum"])
        self.assertIn("referer", props)
        self.assertNotIn("referer", meta["parameters"]["required"])
        for needle in ("404", "403", "referer=", "Cloudflare", "Chrome TLS", "NOT a yaml fetch_mode"):
            self.assertIn(needle, meta["description"], needle)
        self.assertIn("detail page", props["referer"]["description"])
        detail = "https://demo.example/film/100/film-0"
        got = self.run_node([{"tool": "fetch_page", "params": {"url": "https://demo.example/player/oynat/abc", "referer": detail}},
                             {"tool": "fetch_page", "params": {"url": "https://demo.example/player/oynat/abc", "mode": "chrome",
                                                               "referer": ""}}])
        self.assertEqual([r["ok"] for r in got["out"]], [True, True])
        self.assertEqual(self.fake.requests[0]["body"], {"url": "https://demo.example/player/oynat/abc", "referer": detail})
        self.assertEqual(self.fake.requests[1]["body"], {"url": "https://demo.example/player/oynat/abc", "mode": "chrome"})

    def test_grep_page_sends_its_parameters_and_keeps_a_zero_context(self):
        pattern = r"m3u8|\.mp4|file\s*[:=]"
        got = self.run_node([{"tool": "grep_page", "params": {"page_id": "pg_0123456789ab", "pattern": pattern, "context": 0,
                                                              "limit": 5, "flags": "i", "draft_id": "od_ffffffffffff"}},
                             {"tool": "grep_page", "params": {"page_id": "pg_0123456789ab", "pattern": "x", "flags": ""}}])
        self.assertEqual([r["ok"] for r in got["out"]], [True, True])
        self.assertEqual(self.fake.requests[0]["path"], "/grep")
        self.assertEqual(self.fake.requests[0]["body"], {"page_id": "pg_0123456789ab", "pattern": pattern, "context": 0,
                                                         "limit": 5, "flags": "i"})
        self.assertEqual(self.fake.requests[1]["body"], {"page_id": "pg_0123456789ab", "pattern": "x"})

    def test_answer_is_the_sandbox_json(self):
        self.fake.replies["/outline"] = (200, json.dumps({"title": "T", "repeating": [{"selector": "div.card"}]}), 0)
        got = self.run_node([{"tool": "outline_page", "params": {"page_id": "pg_0123456789ab"}}])["out"][0]
        self.assertEqual((got["ok"], got["type"]), (True, "text"))
        self.assertEqual(json.loads(got["text"])["repeating"][0]["selector"], "div.card")

    def test_http_errors_become_readable_exceptions(self):
        self.fake.replies["/fetch"] = (502, json.dumps({"error": {"code": "fetch_failed", "message": "http: HTTP 403"}}), 0)
        self.fake.replies["/query"] = (403, json.dumps({"error": {"code": "forbidden", "message": "missing or invalid X-Onboard-Token"}}), 0)
        self.fake.replies["/outline"] = (500, "<html>Internal Server Error</html>", 0)
        self.fake.replies["/test_config"] = (200, "not json at all", 0)
        got = self.run_node([{"tool": "fetch_page", "params": {"url": "https://x.example/"}},
                             {"tool": "query_html", "params": {"page_id": "pg_0123456789ab", "selector": "a"}},
                             {"tool": "outline_page", "params": {"page_id": "pg_0123456789ab"}},
                             {"tool": "test_config", "params": {"yaml_text": "a: 1"}}])["out"]
        self.assertEqual([g["ok"] for g in got], [False] * 4)
        self.assertEqual(got[0]["error"], "fetch_page: HTTP 502 fetch_failed: http: HTTP 403")
        self.assertIn("HTTP 403 forbidden: missing or invalid X-Onboard-Token", got[1]["error"])
        self.assertTrue(got[2]["error"].startswith("outline_page: HTTP 500:"))
        self.assertIn("Internal Server Error", got[2]["error"])
        self.assertIn("not JSON", got[3]["error"])

    def test_large_answers_are_shrunk_and_stay_json(self):
        big = {"page_id": "pg_0123456789ab", "html_excerpt": "<div>x</div>" * 20000, "items": [{"text": "t" * 5000}] * 60}
        self.fake.replies["/fetch"] = (200, json.dumps(big), 0)
        text = self.run_node([{"tool": "fetch_page", "params": {"url": "https://x.example/"}}])["out"][0]["text"]
        self.assertLessEqual(len(text), 20000)
        data = json.loads(text)
        self.assertEqual(data["page_id"], "pg_0123456789ab")
        self.assertIn("chars]", data["html_excerpt"])
        self.assertLessEqual(len(data["items"]), 40)

    def test_small_answers_are_untouched(self):
        body = {"count": 2, "items": [{"text": "a" * 500}]}
        self.fake.replies["/query"] = (200, json.dumps(body), 0)
        text = self.run_node([{"tool": "query_html", "params": {"page_id": "pg_0123456789ab", "selector": "a"}}])["out"][0]["text"]
        self.assertEqual(json.loads(text), body)

    def test_timeout(self):
        self.fake.replies["/fetch"] = (200, "{}", 2.5)
        started = time.monotonic()
        got = self.run_node([{"tool": "fetch_page", "params": {"url": "https://x.example/"}}],
                            DIZIFLIX_TOOL_TIMEOUT_MS="300")["out"][0]
        self.assertFalse(got["ok"])
        self.assertIn("no answer from the sandbox within 0.3s", got["error"])
        self.assertLess(time.monotonic() - started, 2.4)

    def test_pi_abort_signal_cancels_the_call(self):
        self.fake.replies["/fetch"] = (200, "{}", 2.5)
        got = self.run_node([{"tool": "fetch_page", "params": {"url": "https://x.example/"}, "abort_after_ms": 200}])["out"][0]
        self.assertFalse(got["ok"])
        self.assertEqual(got["error"], "fetch_page: cancelled")

    def test_unreachable_sandbox(self):
        got = self.run_node([{"tool": "list_resolvers"}], DIZIFLIX_SANDBOX_URL="http://127.0.0.1:9/api/onboard/sandbox")["out"][0]
        self.assertFalse(got["ok"])
        self.assertIn("list_resolvers: cannot reach the sandbox at http://127.0.0.1:9/api/onboard/sandbox", got["error"])

    def test_missing_token_and_missing_draft_id(self):
        got = self.run_node([{"tool": "list_resolvers"}], DIZIFLIX_ONBOARD_TOKEN=None)["out"][0]
        self.assertFalse(got["ok"])
        self.assertIn("DIZIFLIX_ONBOARD_TOKEN is not set", got["error"])
        got = self.run_node([{"tool": "submit_draft", "params": {"yaml_text": "a: 1", "site_id_suggestion": "x1"}}],
                            DIZIFLIX_DRAFT_ID=None)["out"][0]
        self.assertFalse(got["ok"])
        self.assertIn("DIZIFLIX_DRAFT_ID is not set", got["error"])
        self.assertEqual(self.fake.requests, [])

    def test_default_base_url_and_trailing_slash(self):
        got = self.run_node([{"tool": "list_resolvers"}], DIZIFLIX_SANDBOX_URL=self.fake.url + "/")["out"][0]
        self.assertTrue(got["ok"])
        self.assertEqual(self.fake.requests[0]["path"], "/resolvers")


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ReadGuardUnderNode(unittest.TestCase):
    """The ``tool_call`` handler of the extension: pi's ``read`` may open the skill directory and nothing else."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="onboard-guard-")
        cls.harness = os.path.join(cls.tmp, "harness.mjs")
        Path(cls.harness).write_text(HARNESS, encoding="utf-8")
        base = Path(cls.tmp).resolve()
        cls.skill = base / "skill"
        (cls.skill / "references").mkdir(parents=True)
        (cls.skill / "SKILL.md").write_text("skill", encoding="utf-8")
        (cls.skill / "references" / "quality.md").write_text("quality", encoding="utf-8")
        cls.outside = base / "outside"
        cls.outside.mkdir()
        (cls.outside / "secret.txt").write_text("secret", encoding="utf-8")
        cls.evil = base / "skill-evil"                    # shares the "skill" prefix: must NOT count as inside
        cls.evil.mkdir()
        (cls.evil / "x.md").write_text("x", encoding="utf-8")
        cls.symlinks = True
        try:
            (cls.skill / "escape").symlink_to(cls.outside, target_is_directory=True)
            (cls.skill / "escape_file.md").symlink_to(cls.outside / "secret.txt")
            (cls.skill / "references" / "inner_link.md").symlink_to(cls.skill / "SKILL.md")   # inside -> inside is fine
            cls.alias = base / "skill_alias"              # the configured dir itself may be a symlink
            cls.alias.symlink_to(cls.skill, target_is_directory=True)
        except OSError:
            cls.symlinks = False

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def guard(self, paths, skill_dir="default", tool="read", cwd=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("DIZIFLIX_")}
        env["NODE_NO_WARNINGS"] = "1"
        if skill_dir == "default":
            env["DIZIFLIX_SKILL_DIR"] = str(self.skill)
        elif skill_dir is not None:
            env["DIZIFLIX_SKILL_DIR"] = str(skill_dir)
        scenario = [{"guard": {"tool": tool, "input": ({"path": p} if p is not None else {})}} for p in paths]
        proc = subprocess.run(["node", self.harness, EXTENSION.as_uri(), json.dumps(scenario)], env=env,
                              capture_output=True, text=True, timeout=60, cwd=str(cwd or self.tmp))
        if proc.returncode != 0 and re.search(r"ERR_UNKNOWN_FILE_EXTENSION|Unknown file extension", proc.stderr):
            self.skipTest("this node cannot strip TypeScript types")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def verdicts(self, paths, **kw):
        """path -> None (allowed) or the block ``reason``."""
        got = self.guard(paths, **kw)
        out = {}
        for path, entry in zip(paths, got["out"]):
            guard = entry["guard"]
            if guard is not None:
                self.assertEqual(set(guard), {"block", "reason"})   # the shape pi expects from a tool_call handler
                self.assertIs(guard["block"], True)
            out[path] = None if guard is None else guard["reason"]
        return out

    def test_the_extension_registers_a_tool_call_guard_next_to_the_tools(self):
        got = self.guard([])
        self.assertEqual(got["handlers"], {"tool_call": 1})
        self.assertEqual(got["names"], TOOLS)

    def test_files_inside_the_skill_dir_are_allowed(self):
        paths = [str(self.skill / "SKILL.md"), str(self.skill / "references" / "quality.md"), str(self.skill),
                 str(self.skill / "references"), str(self.skill / "references" / ".." / "SKILL.md")]
        self.assertEqual(self.verdicts(paths), {p: None for p in paths})

    def test_everything_else_is_blocked_with_a_reason_naming_the_skill_dir(self):
        paths = [str(self.outside / "secret.txt"), "/etc/hostname", "/", str(self.evil / "x.md"), str(self.evil),
                 str(self.skill) + "-evil/x.md"]
        got = self.verdicts(paths)
        for path in paths:
            self.assertIsNotNone(got[path], path)
            self.assertIn(str(self.skill), got[path])
            self.assertIn("limited to the skill directory", got[path])

    def test_dotdot_escapes_are_blocked(self):
        paths = [str(self.skill) + "/../outside/secret.txt", str(self.skill / "references") + "/../../outside/secret.txt",
                 str(self.skill) + "/references/../../skill-evil/x.md", str(self.skill) + "/.."]
        got = self.verdicts(paths)
        for path in paths:
            self.assertIsNotNone(got[path], path)

    def test_symlink_escapes_are_blocked_but_inner_links_work(self):
        if not self.symlinks:
            self.skipTest("symlinks are not available")
        paths = [str(self.skill / "escape"), str(self.skill / "escape" / "secret.txt"), str(self.skill / "escape_file.md")]
        got = self.verdicts(paths + [str(self.skill / "references" / "inner_link.md")])
        for path in paths:
            self.assertIsNotNone(got[path], path)
        self.assertIsNone(got[str(self.skill / "references" / "inner_link.md")])

    def test_the_configured_skill_dir_may_itself_be_a_symlink(self):
        if not self.symlinks:
            self.skipTest("symlinks are not available")
        got = self.verdicts([str(self.skill / "SKILL.md"), str(self.alias / "SKILL.md"), str(self.outside / "secret.txt")],
                            skill_dir=self.alias)
        self.assertIsNone(got[str(self.skill / "SKILL.md")])
        self.assertIsNone(got[str(self.alias / "SKILL.md")])
        self.assertIsNotNone(got[str(self.outside / "secret.txt")])

    def test_relative_missing_and_empty_paths_are_blocked(self):
        got = self.verdicts(["SKILL.md", "references/quality.md", "../outside/secret.txt", str(self.skill / "nope.md"), "", None, "~/x", "@/etc/hostname"])
        for path, reason in got.items():
            self.assertIsNotNone(reason, repr(path))
        # a relative path is taken from the cwd, like pi does: from inside the skill dir it is fine
        self.assertIsNone(self.verdicts(["SKILL.md"], cwd=self.skill)["SKILL.md"])
        self.assertIsNotNone(self.verdicts(["../outside/secret.txt"], cwd=self.skill)["../outside/secret.txt"])

    def test_without_a_skill_dir_every_read_is_blocked(self):
        for skill_dir in (None, "", str(self.skill / "no-such-dir")):
            got = self.verdicts([str(self.skill / "SKILL.md"), "/etc/hostname"], skill_dir=skill_dir)
            for path, reason in got.items():
                self.assertIn("read is disabled", reason or "", (skill_dir, path))

    def test_other_tools_are_not_touched(self):
        got = self.guard([str(self.outside / "secret.txt")], tool="fetch_page")
        self.assertEqual(got["out"], [{"ok": True, "guard": None}])
        self.assertEqual(self.guard([str(self.outside / "secret.txt")], tool="bash")["out"][0]["guard"], None)


if __name__ == "__main__":
    unittest.main()
