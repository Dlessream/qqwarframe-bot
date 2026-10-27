"""世界状态订阅：解析、存储、轮询去重。"""

import os
import tempfile
import time
import unittest

from bot import subscriptions
from bot.subscriptions import (SubscriptionService, SubscriptionStore,
                               parse_kind)


def fissure(node="Mantle", mission="Survival", tier="Lith", hard=False,
            storm=False, minutes=30, ident="n1"):
    return {
        "id": ident, "node": node, "nodeKey": node, "missionType": mission,
        "tier": tier, "enemy": "Grineer", "isHard": hard, "isStorm": storm,
        "expiry": time.strftime("%Y-%m-%dT%H:%M:%S.000Z",
                                time.gmtime(time.time() + minutes * 60)),
        "source": "official",
    }


class FakeWorld:
    def __init__(self):
        self.fissure_rows = []
        self.storm_rows = []
        self.fields = {}

    def fissures(self):
        return list(self.fissure_rows)

    def void_storms(self):
        return list(self.storm_rows)

    def fetch(self, path):
        return self.fields.get(path)


class ParseTest(unittest.TestCase):
    def test_parse_kind(self):
        self.assertEqual(parse_kind("裂缝 生存"), ("fissure", "生存"))
        self.assertEqual(parse_kind("裂缝生存"), ("fissure", "生存"))
        self.assertEqual(parse_kind("仲裁"), ("arbitration", ""))
        self.assertEqual(parse_kind("九重天 冥王"), ("storm", "冥王"))
        self.assertEqual(parse_kind("回廊"), ("circuit", ""))
        self.assertEqual(parse_kind("特惠"), ("deal", ""))
        self.assertEqual(parse_kind("绝路"), (None, "绝路"))
        self.assertEqual(parse_kind(""), (None, ""))

    def test_kind_names(self):
        text = subscriptions.kind_names()
        for label in ("裂缝", "仲裁", "特惠"):
            self.assertIn(label, text)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "subs.json")
        self.store = SubscriptionStore(self.path, max_per_chat=3)

    def test_add_duplicate_list(self):
        sub, created = self.store.add("group", "1", "9", "fissure", "生存")
        self.assertTrue(created)
        self.assertEqual(sub.title, "裂缝·生存")
        same, created2 = self.store.add("group", "1", "9", "fissure", "生存")
        self.assertFalse(created2)
        self.assertEqual(same.id, sub.id)
        self.assertEqual(len(self.store.list_for("group", "1")), 1)
        self.assertEqual(len(self.store.list_for("group", "2")), 0)

    def test_limit(self):
        for kind in ("fissure", "storm", "arbitration"):
            self.store.add("group", "1", "9", kind)
        with self.assertRaises(ValueError):
            self.store.add("group", "1", "9", "alert")

    def test_remove_by_kind_and_all(self):
        self.store.add("group", "1", "9", "fissure")
        self.store.add("group", "1", "9", "arbitration")
        removed = self.store.remove("group", "1", "裂缝")
        self.assertEqual([sub.kind for sub in removed], ["fissure"])
        self.assertEqual(len(self.store.list_for("group", "1")), 1)
        self.assertEqual(len(self.store.remove("group", "1", "all")), 1)
        self.assertEqual(self.store.list_for("group", "1"), [])

    def test_persist(self):
        sub, _ = self.store.add("group", "1", "9", "circuit")
        self.store.mark_seen(sub, ["a", "b"])
        again = SubscriptionStore(self.path)
        rows = again.list_for("group", "1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].kind, "circuit")
        self.assertIn("a", rows[0].seen)

    def test_seen_capped(self):
        store = SubscriptionStore(self.path, seen_limit=5)
        sub, _ = store.add("group", "1", "9", "fissure")
        store.mark_seen(sub, ["k%d" % index for index in range(10)])
        self.assertEqual(len(sub.seen), 5)


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = SubscriptionStore(os.path.join(self.dir, "subs.json"))
        self.world = FakeWorld()
        self.service = SubscriptionService(self.store, self.world)

    def test_seed_does_not_push(self):
        self.world.fissure_rows = [fissure(ident="a")]
        sub, _ = self.store.add("group", "1", "9", "fissure")
        self.service.seed(sub)
        self.assertEqual(self.service.poll(), [])

    def test_new_fissure_pushes_once(self):
        self.world.fissure_rows = [fissure(ident="a")]
        sub, _ = self.store.add("group", "1", "9", "fissure")
        self.service.seed(sub)
        self.assertEqual(self.service.poll(), [])      # 基线
        self.world.fissure_rows = [fissure(ident="a"),
                                   fissure(ident="b", node="E Prime",
                                           mission="Defense")]
        pushes = self.service.poll()
        self.assertEqual(len(pushes), 1)
        chat_id, reply = pushes[0]
        self.assertEqual(chat_id, "1")
        self.assertIn("E Prime", reply.text)
        self.assertNotIn("Mantle", reply.text)         # 只推新增的那条
        self.assertEqual(self.service.poll(), [])      # 不重复推

    def test_first_poll_after_restart_only_seeds(self):
        self.world.fissure_rows = [fissure(ident="a")]
        self.store.add("group", "1", "9", "fissure")
        fresh = SubscriptionService(self.store, self.world)
        self.assertEqual(fresh.poll(), [])             # 进程重启不补推
        self.world.fissure_rows = [fissure(ident="a"), fissure(ident="b")]
        self.assertEqual(len(fresh.poll()), 1)

    def test_keyword_filter(self):
        self.world.fissure_rows = []
        sub, _ = self.store.add("group", "1", "9", "fissure", "生存")
        self.service.seed(sub)
        self.service.poll()
        self.world.fissure_rows = [
            fissure(ident="d", mission="Defense"),
            fissure(ident="s", mission="Survival"),
        ]
        pushes = self.service.poll()
        self.assertEqual(len(pushes), 1)
        self.assertIn("生存", pushes[0][1].text)
        self.assertNotIn("防御", pushes[0][1].text)

    def test_expired_skipped(self):
        sub, _ = self.store.add("group", "1", "9", "fissure")
        self.service.seed(sub)
        self.service.poll()
        self.world.fissure_rows = [fissure(ident="old", minutes=-5)]
        self.assertEqual(self.service.poll(), [])

    def test_storm_not_mixed_with_fissure(self):
        self.world.fissure_rows = [fissure(ident="f1")]
        self.world.storm_rows = [fissure(ident="s1", storm=True, node="Rian")]
        sub, _ = self.store.add("group", "1", "9", "storm")
        self.service.seed(sub)
        self.service.poll()
        self.world.storm_rows.append(fissure(ident="s2", storm=True, node="Korms"))
        self.world.fissure_rows.append(fissure(ident="f2"))
        pushes = self.service.poll()
        self.assertEqual(len(pushes), 1)
        self.assertIn("Korms", pushes[0][1].text)
        self.assertNotIn("f2", pushes[0][1].text)


if __name__ == "__main__":
    unittest.main()
