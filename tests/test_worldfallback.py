import time
import unittest

from bot import worldfallback
from bot.net import HttpError
from bot.worldstate import WorldStateClient, WorldStateError


class FakeHttp:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_json(self, url, headers=None):
        self.calls.append(url)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def ms(seconds):
    return {"$date": {"$numberLong": str(int(seconds * 1000))}}


OFFICIAL_PAYLOAD = {
    "VoidTraders": [{
        "Activation": ms(1793365200), "Expiry": ms(1793538000),
        "Character": "EvilBaroWeek3", "Node": "MercuryHUB",
        "Manifest": [
            {"ItemType": "/Lotus/StoreItems/Weapons/VoidTrader/VTDetron",
             "PrimePrice": 500, "RegularPrice": 200000},
            {"ItemType": "/Lotus/StoreItems/Upgrades/Mods/Melee/"
                          "WeaponEventSlashDamageMod",
             "PrimePrice": 375, "RegularPrice": 150000},
        ],
    }],
    "Sorties": [{
        "Boss": "SORTIE_BOSS_RUK", "Activation": ms(1792944000),
        "Expiry": ms(1793030400),
        "Variants": [
            {"missionType": "MT_INTEL", "node": "SolNode67",
             "modifierType": "SORTIE_MODIFIER_HAZARD_RADIATION"},
            {"missionType": "MT_RESCUE", "node": "SolNode301",
             "modifierType": "SORTIE_MODIFIER_IMPACT"},
        ],
    }],
    "LiteSorties": [{
        "Boss": "SORTIE_BOSS_BOREAL",
        "Reward": "/Lotus/Types/Game/MissionDecks/ArchonSortieRewards",
        "Activation": ms(time.time() - 3600),
        "Expiry": ms(time.time() + 86400),
        "Missions": [
            {"missionType": "MT_INTEL", "node": "SolNode75"},
            {"missionType": "MT_DEFENSE", "node": "SolNode39"},
            {"missionType": "MT_ASSASSINATION", "node": "SolNode24"},
        ],
    }],
    "EndlessXpSchedule": [{
        "Activation": ms(time.time() - 3600),
        "Expiry": ms(time.time() + 86400),
        "CategoryChoices": [
            {"Category": "EXC_NORMAL", "Choices": ["Garuda", "Baruuk", "Hildryn"]},
            {"Category": "EXC_HARD", "Choices": ["Lex", "Magistar", "Boltor"]},
        ],
    }],
    "Alerts": [{
        "_id": {"$oid": "abc"},
        "Activation": ms(1793012400), "Expiry": ms(1793017800),
        "MissionInfo": {
            "missionType": "MT_MOBILE_DEFENSE", "faction": "FC_INFESTATION",
            "location": "SolNode164",
            "missionReward": {"credits": 17000, "countedItems": [
                {"ItemType": "/Lotus/Types/Items/FusionTreasures/OroFusex",
                 "ItemCount": 50}]},
        },
    }],
    "Invasions": [{
        "_id": {"$oid": "inv1"}, "Faction": "FC_CORPUS",
        "DefenderFaction": "FC_GRINEER", "Node": "SolNode137",
        "Count": -24500, "Goal": 49000,
        "AttackerReward": {"countedItems": [
            {"ItemType": "/Lotus/Types/Recipes/Weapons/WeaponParts/SnipetronVandalStock",
             "ItemCount": 1}]},
        "DefenderReward": {"countedItems": []},
    }],
    "ActiveMissions": [{
        "Node": "SolNode128", "MissionType": "MT_EXTERMINATION",
        "Modifier": "VoidT1", "Hard": False,
        "Expiry": ms(time.time() + 600),
    }],
    "DailyDeals": [{
        "StoreItem": "/Lotus/StoreItems/Weapons/Tenno/Akimbo/AkimboShotGun",
        "Activation": ms(time.time() - 600),
        "Expiry": ms(time.time() + 3600),
        "Discount": 20, "OriginalPrice": 225, "SalePrice": 180,
        "AmountTotal": 200, "AmountSold": 14,
    }, {
        "StoreItem": "/Lotus/StoreItems/Weapons/Tenno/ExpiredGun",
        "Activation": ms(time.time() - 7200),
        "Expiry": ms(time.time() - 3600),
        "Discount": 50, "OriginalPrice": 100, "SalePrice": 50,
    }],
    "Events": [{
        "Prop": "https://discord.com/invite/playwarframe",
        "Messages": [
            {"LanguageCode": "en", "Message": "Join the official Warframe Discord server"},
            {"LanguageCode": "zh", "Message": "/Lotus/Language/CommunityMessages/JoinDiscord"},
        ],
        "Priority": False, "Community": True,
    }, {
        "Prop": "https://wiki.warframe.com/",
        "Messages": [
            {"LanguageCode": "en", "Message": "Check out the official Warframe Wiki "},
        ],
        "Priority": False, "Community": True,
    }],
    "SyndicateMissions": [{
        "Tag": "CetusSyndicate", "Activation": ms(time.time() - 600),
        "Expiry": ms(time.time() + 4000),
        "Jobs": [{"jobType": "/Lotus/Types/Gameplay/Eidolon/Jobs/AttritionBountySab",
                  "minEnemyLevel": 5, "maxEnemyLevel": 15,
                  "xpAmounts": [460, 460, 460]}],
    }, {
        "Tag": "SolarisSyndicate", "Activation": ms(time.time() - 600),
        "Expiry": ms(time.time() + 3000),
        "Jobs": [{"jobType": "/Lotus/Types/Gameplay/Venus/Jobs/VenusBountyCap",
                  "minEnemyLevel": 10, "maxEnemyLevel": 30}],
    }],
}

ORACLE_WEEKLY = {
    "expiry": 1793577600,
    "labConquestMissions": [
        {"type": "DualDefense", "variant": "HostileSecurity",
         "conditions": ["VoidAberration", "Quicksand"]},
    ],
    "labConquestFrameVariables": ["AntiGuard", "DecayingFlesh"],
    "hexConquestMissions": [
        {"type": "Survival", "variant": "TechrotConjunction",
         "conditions": ["AcceleratedEnemies"]},
    ],
    "hexConquestFrameVariables": ["OverSensitive"],
}


class CycleMathTest(unittest.TestCase):
    def test_cetus_day(self):
        now = 1000000.0
        cycle = worldfallback.cetus_cycle(now, now + 4000)
        self.assertEqual(cycle["state"], "day")
        self.assertTrue(cycle["isDay"])
        self.assertEqual(cycle["timeLeft"], "16m 40s")

    def test_cetus_night(self):
        now = 1000000.0
        cycle = worldfallback.cetus_cycle(now, now + 1800)
        self.assertEqual(cycle["state"], "night")
        self.assertEqual(cycle["timeLeft"], "30m 0s")

    def test_cetus_stale_table_wraps(self):
        now = 1000000.0
        cycle = worldfallback.cetus_cycle(now, now - 100)
        self.assertIn(cycle["state"], ("day", "night"))
        self.assertNotEqual(cycle["timeLeft"], "0s")

    def test_vallis_start_is_warm(self):
        cycle = worldfallback.vallis_cycle(worldfallback.VALLIS_START_MS / 1000.0)
        self.assertEqual(cycle["state"], "warm")
        self.assertEqual(cycle["timeLeft"], "6m 40s")

    def test_vallis_half_loop_is_cold(self):
        cycle = worldfallback.vallis_cycle(
            (worldfallback.VALLIS_START_MS + 800000) / 1000.0)
        self.assertEqual(cycle["state"], "cold")

    def test_cambion_follows_cetus(self):
        self.assertEqual(
            worldfallback.cambion_cycle({"isDay": True})["state"], "fass")
        self.assertEqual(
            worldfallback.cambion_cycle({"isDay": False})["state"], "vome")
        self.assertIsNone(worldfallback.cambion_cycle(None))


class BuildStateTest(unittest.TestCase):
    def setUp(self):
        self.source = worldfallback.OfficialWorldState(
            http=FakeHttp(OFFICIAL_PAYLOAD),
            oracle_http=FakeHttp(ORACLE_WEEKLY),
            nodes={},
            ttl=30,
        )

    def test_void_trader(self):
        trader = self.source.state()["voidTrader"]
        self.assertEqual(trader["character"], "Baro Ki'Teer")
        self.assertEqual(trader["location"], "Larunda Relay (Mercury)")
        self.assertTrue(trader["expiry"])

    def test_void_trader_inventory_from_manifest(self):
        """官方没有名称，但 Manifest 里有 uniqueName 与两种价格。"""
        trader = self.source.state()["voidTrader"]
        self.assertEqual(len(trader["inventory"]), 2)
        first = trader["inventory"][0]
        self.assertEqual(first["item"],
                         "/Lotus/StoreItems/Weapons/VoidTrader/VTDetron")
        self.assertEqual(first["ducats"], 500)
        self.assertEqual(first["credits"], 200000)

    def test_void_trader_without_manifest(self):
        row = {"ItemType": "x"}
        self.assertEqual(worldfallback.trader_inventory(row), [])
        self.assertEqual(worldfallback.trader_inventory({}), [])
        self.assertEqual(worldfallback.trader_inventory({"Manifest": "bad"}), [])

    def test_sortie_modifiers_localized(self):
        sortie = self.source.state()["sortie"]
        self.assertEqual(sortie["boss"], "Sargas Ruk")
        self.assertEqual(sortie["variants"][0]["modifierDescription"], "环境：辐射危害")
        self.assertEqual(sortie["variants"][1]["modifierDescription"], "物理强化：冲击")
        self.assertEqual(sortie["variants"][1]["missionType"], "Rescue")

    def test_alerts_reward(self):
        alert = self.source.state()["alerts"][0]
        self.assertEqual(alert["mission"]["type"], "Mobile Defense")
        self.assertEqual(alert["mission"]["faction"], "Infested")
        self.assertEqual(alert["mission"]["reward"]["credits"], 17000)
        self.assertEqual(
            alert["mission"]["reward"]["countedItems"][0]["count"], 50)

    def test_invasions_completion(self):
        row = self.source.state()["invasions"][0]
        self.assertEqual(row["attacker"]["faction"], "Corpus")
        self.assertEqual(row["defender"]["faction"], "Grineer")
        self.assertEqual(row["completion"], 50.0)
        drops = row["attacker"]["reward"]["countedItems"]
        self.assertEqual(drops[0]["type"], "Snipetron Vandal Stock")
        self.assertEqual(drops[0]["count"], 1)

    def test_bounties(self):
        rows = self.source.state()["syndicateMissions"]
        names = [row["syndicate"] for row in rows]
        self.assertEqual(names, ["Ostrons", "Solaris United"])
        self.assertEqual(rows[0]["jobs"][0]["type"], "破坏克隆尼的补给线")
        self.assertEqual(rows[0]["jobs"][0]["enemyLevels"], [5, 15])
        # 官方 xpAmounts 透传为 standingStages，长度=帐篷卡阶段数
        self.assertEqual(rows[0]["jobs"][0]["standingStages"], [460, 460, 460])
        # 福尔图娜这条 jobType 不在官方目标表里，只回退到任务类型
        self.assertEqual(rows[1]["jobs"][0]["type"], "捕获")

    def test_archimedeas(self):
        rows = self.source.state()["archimedeas"]
        self.assertEqual([row["typeKey"] for row in rows],
                         ["C T_ L A B", "C T_ H E X"])
        self.assertEqual(rows[0]["missions"][0]["missionType"], "Dual Defense")
        self.assertEqual(rows[0]["missions"][0]["deviation"]["name"],
                         "Hostile Security")
        self.assertEqual(rows[0]["personalModifiers"][0]["name"], "Anti Guard")

    def test_fissures_present(self):
        rows = self.source.state()["fissures"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tier"], "Lith")

    def test_daily_deals_from_official(self):
        rows = self.source.state()["dailyDeals"]
        # 过期那条被剔除
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["discount"], 20)
        self.assertEqual(rows[0]["originalPrice"], 225)
        self.assertEqual(rows[0]["salePrice"], 180)
        self.assertTrue(rows[0]["expiry"])

    def test_news_from_official_events(self):
        rows = self.source.state()["news"]
        self.assertEqual(len(rows), 2)
        by_msg = {row["message"]: row for row in rows}
        discord = by_msg["Join the official Warframe Discord server"]
        self.assertEqual(discord["link"], "https://discord.com/invite/playwarframe")
        self.assertTrue(discord["community"])
        # 英文正文末尾的空格被清理
        wiki = by_msg["Check out the official Warframe Wiki"]
        self.assertEqual(wiki["link"], "https://wiki.warframe.com/")

    def test_events_from_official(self):
        # OFFICIAL_PAYLOAD 没有 Goals，官方活动为空列表而不是 None
        self.assertEqual(self.source.state()["events"], [])

    def test_archon_hunt_from_lite_sorties(self):
        hunt = self.source.state()["archonHunt"]
        self.assertEqual(hunt["boss"], "Archon Boreal")
        self.assertEqual(hunt["faction"], "Corpus")
        self.assertEqual(len(hunt["missions"]), 3)
        self.assertEqual(hunt["missions"][0]["type"], "Spy")
        self.assertTrue(hunt["expiry"])

    def test_circuit_from_endless_xp_schedule(self):
        circuit = self.source.state()["duviriCycle"]
        self.assertEqual([g["category"] for g in circuit["choices"]],
                         ["normal", "hard"])
        self.assertEqual(circuit["choices"][0]["choices"],
                         ["Garuda", "Baruuk", "Hildryn"])
        self.assertEqual(circuit["choices"][1]["choices"],
                         ["Lex", "Magistar", "Boltor"])

    def test_steel_path_rotation_is_computed(self):
        steel = self.source.state()["steelPath"]
        self.assertEqual(len(steel["rotation"]), 8)
        self.assertEqual(steel["currentReward"]["name"],
                         worldfallback.STEEL_PATH_ROTATION[
                             steel["rotation"].index(steel["currentReward"])
                         ]["name"])
        self.assertTrue(steel["currentReward"]["cost"] > 0)
        self.assertTrue(steel["expiry"])
        self.assertTrue(steel["incursions"]["expiry"])

    def test_steel_path_index_follows_wfcd_cycle(self):
        start = worldfallback.STEEL_PATH_START
        week = worldfallback.STEEL_PATH_WEEK
        for offset, expected in ((0, 0), (week, 1), (7 * week, 7)):
            reward = worldfallback.steel_path_offering(start + offset + 1)
            self.assertEqual(
                reward["currentReward"],
                worldfallback.STEEL_PATH_ROTATION[expected])

    def test_missing_optional_modules_stay_none(self):
        source = worldfallback.OfficialWorldState(
            http=FakeHttp({"VoidTraders": []}),
            oracle_http=FakeHttp({}),
            nodes={},
            ttl=30,
        )
        state = source.state()
        self.assertIsNone(state["archonHunt"])
        self.assertIsNone(state["duviriCycle"])

    def test_cache_avoids_refetch(self):
        http = FakeHttp(OFFICIAL_PAYLOAD)
        source = worldfallback.OfficialWorldState(
            http=http, oracle_http=FakeHttp(ORACLE_WEEKLY), nodes={}, ttl=30)
        source.state()
        source.state()
        self.assertEqual(len(http.calls), 1)


class ClientFallbackTest(unittest.TestCase):
    def make(self, main_error=None):
        client = WorldStateClient({
            "base_url": "http://example.invalid",
            "language": "zh",
            "official_fallback": True,
            "official_proxy": "none",
        })
        client.http = FakeHttp(main_error or HttpError("blocked", status=403))
        client.official_http = FakeHttp(OFFICIAL_PAYLOAD)
        client.oracle_http = FakeHttp(ORACLE_WEEKLY)
        return client

    def test_state_from_official(self):
        state = self.make().fetch("")
        self.assertEqual(state["source"], "official")
        self.assertEqual(state["cetusCycle"]["state"], "day")
        self.assertEqual(state["vallisCycle"]["state"] in ("warm", "cold"), True)

    def test_field_from_official(self):
        self.assertEqual(self.make().fetch("sortie")["boss"], "Sargas Ruk")

    def test_cycle_commands_render(self):
        from bot import present
        client = self.make()
        reply = present.cycles_reply(
            client.fetch("cetusCycle"), client.fetch("vallisCycle"),
            client.fetch("cambionCycle"))
        self.assertIn("白天", reply.text)
        self.assertIn("法斯", reply.text)

    def test_arbitration_stub_falls_back(self):
        arbitration = self.make().arbitration()
        self.assertTrue(arbitration.expired)

    def test_cooldown_skips_main_source(self):
        client = self.make()
        client._get_uncached("")
        client._get_uncached("sortie")
        self.assertEqual(len(client.http.calls), 1)

    def test_unknown_field_reports_clearly(self):
        client = self.make()
        with self.assertRaises(WorldStateError) as ctx:
            client.fetch("noSuchModule")
        self.assertIn("第三方数据站", str(ctx.exception))

    def test_fallback_failure_returns_friendly_error(self):
        client = self.make()
        client.official_http = FakeHttp(HttpError("down", status=0))
        with self.assertRaises(WorldStateError) as ctx:
            client.fetch("")
        self.assertIn("Cloudflare", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
