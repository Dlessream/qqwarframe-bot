import json
import os
import tempfile
import time
import unittest

from bot.commands import rotation_shop
from bot.net import HttpError
from bot.rotation import (ACRITHIS_ANCHOR, CODA_BATCHES, ELEANOR_ANCHOR,
                          GLAST_ANCHOR, RotationClient, acrithis_next,
                          eleanor_state, glast_next, next_slot, parse_acrithis,
                          parse_bonus_rows, parse_coda, parse_glast,
                          strip_jina, _utc)

ACRITHIS_RAW = """<!-- Valid item names for AcrithisItem1-5 below (must match exactly, including capitalization):
Orokin Reactor
Forma
-->{{#vardefine:AcrithisObserved|October 26, 2026}}<!--
-->{{#vardefine:AcrithisItem1|Shotgun Riven Mod}}<!--
-->{{#vardefine:AcrithisItem2|Forma}}<!--
-->{{#vardefine:AcrithisItem3|Rifle Riven Mod}}<!--
-->{{#vardefine:AcrithisItem4|Companion Weapon Riven Mod}}<!--
-->{{#vardefine:AcrithisItem5|10,000 Kuva}}<!--
--><noinclude>...</noinclude>
"""

GLAST_RAW = """<section begin="glast_tenet_melee_timer" />
<table class="wikitable" style="text-align: center; margin: auto;">
    <tr>
        <th>Weapon</th>
        <th>Element</th>
        <th>Bonus %</th>
    </tr>
    <tr><td>{{Weapon|Tenet Ferrox}}</td><td>{{D|Radiation}}</td><td>{{ValenceBonusPercentageColor|38.4}}</td></tr>
    <tr><td>{{Weapon|Tenet Grigori}}</td><td>{{D|Magnetic}}</td><td>{{ValenceBonusPercentageColor|42.8}}</td></tr>
    <tr><td>{{Weapon|Tenet Livia}}</td><td>{{D|Radiation}}</td><td>{{ValenceBonusPercentageColor|25}}</td></tr>
    <tr><td>{{Weapon|Tenet Exec}}</td><td>{{D|Radiation}}</td><td>{{ValenceBonusPercentageColor|43.9}}</td></tr>
    <tr><td>{{Weapon|Tenet Agendus}}</td><td>{{D|Magnetic}}</td><td>{{ValenceBonusPercentageColor|40.9}}</td></tr>
</table>
<section end="glast_tenet_melee_timer" />
"""

CODA_RAW = """<section begin="eleanor_coda_timer" />
{{#vardefine:RotatingRewardsIndexNormal|{{#expr: floor ((({{#time: U | now }} - {{#time: U | 2025-03-18 }}) mod (86400 * 8)) / (86400 * 4))}}}}
<table class="wikitable">
    <tr><th>Weapon (Batch A)</th><th>Element</th><th>Bonus %</th></tr>
    <tr><td>Coda Catabolyst</td><td>{{D|Toxin}}</td><td>{{ValenceBonusPercentageColor|29.7}}</td></tr>
    <tr><td>Coda Hema</td><td>{{D|Cold}}</td><td>{{ValenceBonusPercentageColor|25.6}}</td></tr>
    <tr><td>Coda Mire</td><td>{{D|Magnetic}}</td><td>{{ValenceBonusPercentageColor|33.0}}</td></tr>
    <tr><td>Coda Motovore</td><td>{{D|Electricity}}</td><td>{{ValenceBonusPercentageColor|49.8}}</td></tr>
    <tr><td>Coda Pox</td><td>{{D|Toxin}}</td><td>{{ValenceBonusPercentageColor|54.3}}</td></tr>
    <tr><td>Coda Sporothrix</td><td>{{D|Radiation}}</td><td>{{ValenceBonusPercentageColor|28.3}}</td></tr>
    <tr><td>Dual Coda Torxica</td><td>{{D|Toxin}}</td><td>{{ValenceBonusPercentageColor|40.1}}</td></tr>
</table>
<table class="wikitable">
    <tr><th>Weapon (Batch B)</th><th>Element</th><th>Bonus %</th></tr>
    <tr><td>Coda Bassocyst</td><td>{{D|Impact}}</td><td>{{ValenceBonusPercentageColor|46.7}}</td></tr>
    <tr><td>Coda Bubonico</td><td>{{D|Heat}}</td><td>{{ValenceBonusPercentageColor|25.1}}</td></tr>
    <tr><td>Unknown Weapon</td><td>{{D|Heat}}</td><td>{{ValenceBonusPercentageColor|99.9}}</td></tr>
</table>
<section end="eleanor_coda_timer" />
"""

REAL_PAGES = {"Acrithis/Current_Offerings": ACRITHIS_RAW,
              "Tenet_Weapons": GLAST_RAW,
              "Coda_Weapons": CODA_RAW}


class ParseTest(unittest.TestCase):
    def test_acrithis_offerings(self):
        data = parse_acrithis(ACRITHIS_RAW)
        self.assertEqual(data["observed"], "October 26, 2026")
        self.assertEqual(data["items"], ["Shotgun Riven Mod", "Forma",
                                         "Rifle Riven Mod",
                                         "Companion Weapon Riven Mod",
                                         "10,000 Kuva"])

    def test_acrithis_empty(self):
        self.assertIsNone(parse_acrithis("nothing here"))

    def test_bonus_rows(self):
        rows = parse_bonus_rows(GLAST_RAW)
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0], ("Tenet Ferrox", "Radiation", 38.4))
        self.assertEqual(rows[2][2], 25.0)

    def test_glast_only_known_weapons(self):
        rows = parse_glast(GLAST_RAW)
        self.assertEqual([row[0] for row in rows],
                         ["Tenet Ferrox", "Tenet Grigori", "Tenet Livia",
                          "Tenet Exec", "Tenet Agendus"])

    def test_coda_batches_split(self):
        batches = parse_coda(CODA_RAW)
        self.assertEqual(len(batches["A"]), 7)
        self.assertEqual([row[0] for row in batches["B"]],
                         ["Coda Bassocyst", "Coda Bubonico"])
        self.assertEqual(batches["A"][4], ("Coda Pox", "Toxin", 54.3))

    def test_strip_jina_header(self):
        raw = ("Title: Acrithis\n\nURL Source: https://wiki.warframe.com/x\n\n"
               "Markdown Content:\n{{#vardefine:AcrithisObserved|October 26, 2026}}")
        self.assertTrue(strip_jina(raw).startswith("{{#vardefine"))
        self.assertEqual(strip_jina("plain"), "plain")


class ScheduleTest(unittest.TestCase):
    def test_next_slot_boundaries(self):
        self.assertEqual(next_slot(GLAST_ANCHOR, 4 * 86400, GLAST_ANCHOR),
                         GLAST_ANCHOR + 4 * 86400)
        self.assertEqual(next_slot(GLAST_ANCHOR, 4 * 86400,
                                   GLAST_ANCHOR + 4 * 86400),
                         GLAST_ANCHOR + 8 * 86400)
        self.assertEqual(next_slot(GLAST_ANCHOR, 4 * 86400, 0), GLAST_ANCHOR)

    def test_eleanor_alternates(self):
        self.assertEqual(eleanor_state(ELEANOR_ANCHOR),
                         ("A", ELEANOR_ANCHOR + 4 * 86400))
        self.assertEqual(eleanor_state(ELEANOR_ANCHOR + 4 * 86400),
                         ("B", ELEANOR_ANCHOR + 8 * 86400))
        self.assertEqual(eleanor_state(ELEANOR_ANCHOR + 8 * 86400),
                         ("A", ELEANOR_ANCHOR + 12 * 86400))

    def test_eleanor_before_anchor(self):
        self.assertEqual(eleanor_state(ELEANOR_ANCHOR - 10),
                         ("A", ELEANOR_ANCHOR))

    def test_acrithis_resets_monday(self):
        # 2026-10-26 是周一，下一次轮换应当落在 11-02 00:00 UTC
        self.assertEqual(acrithis_next(_utc(2026, 10, 26)), _utc(2026, 11, 2))
        self.assertEqual(acrithis_next(ACRITHIS_ANCHOR),
                         ACRITHIS_ANCHOR + 7 * 86400)

    def test_glast_next_is_four_days(self):
        self.assertEqual(glast_next(GLAST_ANCHOR + 100) - (GLAST_ANCHOR + 100),
                         4 * 86400 - 100)


class FakeHttp:
    def __init__(self, pages=None, fail=False):
        self.pages = pages or {}
        self.fail = fail
        self.calls = []

    def get_text(self, url, headers=None):
        self.calls.append(url)
        if self.fail:
            raise HttpError("HTTP 403 Forbidden", status=403, url=url)
        for key, body in self.pages.items():
            if key.lower() in url.lower():
                return body
        raise HttpError("HTTP 404", status=404, url=url)


def build_client(tmpdir, pages=None, fail=False, **config):
    holder = {}

    def factory(proxy):
        client = holder.get(proxy)
        if client is None:
            client = FakeHttp(pages, fail=fail)
            holder[proxy] = client
        return client

    settings = {"cache_file": os.path.join(tmpdir, "rotation_cache.json"),
                "use_jina": False, "proxy": "", "proxy_mode": "direct"}
    settings.update(config)
    client = RotationClient(settings, http_factory=factory)
    return client, holder


class ClientTest(unittest.TestCase):
    def test_refresh_fills_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, _holder = build_client(tmp, REAL_PAGES)
            self.assertTrue(client.refresh())
            acrithis = client.acrithis_view()
            self.assertEqual([row["name"] for row in acrithis["rows"]][:2],
                             ["霰弹枪裂罅 MOD", "福马蓝图"])
            self.assertEqual(acrithis["rows"][1]["cost"], 10)

            glast = client.glast_view()
            self.assertTrue(glast["complete"])
            self.assertEqual(glast["rows"][0]["name"], "信条·铁晶磁轨炮")
            self.assertEqual(glast["rows"][0]["element"], "辐射")
            self.assertEqual(glast["rows"][0]["percent"], 38.4)

            eleanor = client.eleanor_view()
            self.assertIn(eleanor["batch"], ("A", "B"))
            self.assertEqual(len(eleanor["rows"]), 7)

    def test_refresh_respects_ttl(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, holder = build_client(tmp, REAL_PAGES)
            client.refresh()
            calls = sum(len(http.calls) for http in holder.values())
            self.assertFalse(client.refresh())
            self.assertEqual(sum(len(http.calls) for http in holder.values()),
                             calls)
            client.refresh(force=True)
            self.assertGreater(sum(len(http.calls) for http in holder.values()),
                               calls)

    def test_survives_fetch_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, _holder = build_client(tmp, REAL_PAGES, fail=True)
            self.assertFalse(client.refresh())
            status = client.status()
            self.assertFalse(status["acrithis"])
            self.assertEqual(status["glast"], 0)
            self.assertTrue(status["error"])

    def test_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rotation_cache.json")
            client, _holder = build_client(tmp, REAL_PAGES)
            client.refresh()
            with open(path, encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertTrue(saved["data"]["acrithis"]["items"])

            again, holder = build_client(tmp, {}, fail=True)
            acrithis = again.acrithis_view()
            self.assertEqual(acrithis["rows"][0]["name"], "霰弹枪裂罅 MOD")
            glast = again.glast_view()
            self.assertEqual(glast["rows"][2]["element"], "辐射")

    def test_jina_fallback(self):
        """直连 403 时应当改走 r.jina.ai。"""

        class Mixed(FakeHttp):
            def get_text(self, url, headers=None):
                self.calls.append(url)
                if "r.jina.ai" in url:
                    return "Title: x\n\nMarkdown Content:\n" + GLAST_RAW
                raise HttpError("HTTP 403", status=403, url=url)

        holder = {}

        def factory(proxy):
            client = holder.get(proxy)
            if client is None:
                client = Mixed()
                holder[proxy] = client
            return client

        with tempfile.TemporaryDirectory() as tmp:
            client = RotationClient(
                {"cache_file": os.path.join(tmp, "c.json"), "use_jina": True,
                 "proxy": "", "proxy_mode": "direct"}, http_factory=factory)
            client.refresh(force=True)
            glast = client.glast_view()
            self.assertEqual(glast["rows"][0]["element"], "辐射")
            tried = [url for http in holder.values() for url in http.calls]
            self.assertTrue(any("r.jina.ai" in url for url in tried))


class ShopRoutingTest(unittest.TestCase):
    def test_keywords(self):
        self.assertEqual(rotation_shop(""), "all")
        self.assertEqual(rotation_shop("全部"), "all")
        self.assertEqual(rotation_shop("言录使"), "acrithis")
        self.assertEqual(rotation_shop("acrithis"), "acrithis")
        self.assertEqual(rotation_shop("格拉斯"), "glast")
        self.assertEqual(rotation_shop("埃尔戈·格拉斯"), "glast")
        self.assertEqual(rotation_shop("埃莉诺"), "eleanor")
        self.assertEqual(rotation_shop("终幕武器"), "eleanor")
        self.assertIsNone(rotation_shop("奸商"))

    def test_coda_batches_have_seven_each(self):
        self.assertEqual(len(CODA_BATCHES["A"]), 7)
        self.assertEqual(len(CODA_BATCHES["B"]), 7)


if __name__ == "__main__":
    unittest.main()
