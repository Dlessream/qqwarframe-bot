import json
import os
import shutil
import tempfile
import unittest

from bot.alerts import Alert, AlertStore, chat_key


class AlertStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wf-alerts-")
        self.path = os.path.join(self.tmp, "alerts.json")
        self.store = AlertStore(self.path, max_per_chat=3)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_chat_key(self):
        self.assertEqual(chat_key("group", "123"), "g:123")
        self.assertEqual(chat_key("private", "123"), "p:123")

    def test_add_and_list(self):
        alert = self.store.add("group", "1", "9", "rubico_prime_set",
                               "绝路 Prime 一套", 50, "pc")
        self.assertEqual(len(alert.id), 4)
        alerts = self.store.list_for("group", "1")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].target, 50)
        self.assertTrue(os.path.isfile(self.path))

    def test_persist_and_reload(self):
        self.store.add("group", "1", "9", "a", "A", 10, "pc")
        reloaded = AlertStore(self.path)
        self.assertEqual(len(reloaded.list_for("group", "1")), 1)
        self.assertEqual(reloaded.active()[0].slug, "a")

    def test_duplicate_rejected(self):
        self.store.add("group", "1", "9", "a", "A", 10, "pc")
        with self.assertRaises(ValueError):
            self.store.add("group", "1", "9", "a", "A", 8, "pc")

    def test_limit_per_chat(self):
        for index in range(3):
            self.store.add("group", "1", "9", "slug%d" % index, "N%d" % index, 10, "pc")
        with self.assertRaises(ValueError):
            self.store.add("group", "1", "9", "another", "X", 10, "pc")

    def test_remove_one_and_all(self):
        first = self.store.add("group", "1", "9", "a", "A", 10, "pc")
        self.store.add("group", "1", "9", "b", "B", 10, "pc")
        self.assertEqual(self.store.remove("group", "1", first.id), 1)
        self.assertEqual(len(self.store.list_for("group", "1")), 1)
        self.assertEqual(self.store.remove("group", "1", "all"), 1)
        self.assertEqual(self.store.list_for("group", "1"), [])

    def test_remove_other_chat_noop(self):
        self.store.add("group", "1", "9", "a", "A", 10, "pc")
        self.assertEqual(self.store.remove("group", "2", "all"), 0)

    def test_update_price(self):
        alert = self.store.add("group", "1", "9", "a", "A", 10, "pc")
        self.store.set_price(alert, 42)
        reloaded = AlertStore(self.path)
        self.assertEqual(reloaded.active()[0].last_price, 42)

    def test_deactivate(self):
        alert = self.store.add("group", "1", "9", "a", "A", 10, "pc")
        alert.active = False
        self.store.update(alert)
        self.assertEqual(self.store.active(), [])
        self.assertTrue(os.path.isfile(self.path))

    def test_broken_file_ignored(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        store = AlertStore(self.path)
        self.assertEqual(store.active(), [])

    def test_alert_roundtrip(self):
        alert = Alert("abcd", "group", "1", "9", "slug", "名字", 20, "pc", 1.0)
        restored = Alert.from_dict(alert.to_dict())
        self.assertEqual(restored.id, "abcd")
        self.assertEqual(restored.target, 20)


if __name__ == "__main__":
    unittest.main()
