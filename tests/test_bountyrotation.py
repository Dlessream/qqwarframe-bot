import unittest

from bot import bountyrotation as br


class BountyRotationTest(unittest.TestCase):
    def test_pool_of_job_unique_name(self):
        self.assertEqual(br.pool_of_job({
            "uniqueName": "/Lotus/Types/Game/MissionDecks/EidolonJobMissionRewards/TierATableARewards"
        }), "A")
        self.assertEqual(br.pool_of_job({
            "rewards": "/Lotus/Types/Game/MissionDecks/EidolonJobMissionRewards/TierBTableCRewards"
        }), "C")

    def test_pool_of_job_rewards_string(self):
        self.assertEqual(br.pool_of_job({"rewards": "TierCTableBRewards"}), "B")

    def test_pool_of_job_empty(self):
        self.assertEqual(br.pool_of_job({}), "")
        self.assertEqual(br.pool_of_job(None), "")

    def test_pool_of_drops(self):
        # A 池标志：Redirection / Oxium
        self.assertEqual(br.pool_of_drops({
            "rewardPoolDrops": [
                {"item": "Redirection"}, {"item": "Oxium"}, {"item": "Iradite"},
            ]
        }), "A")
        # B 池标志：Pressure Point / Cryotic
        self.assertEqual(br.pool_of_drops({
            "rewardPoolDrops": [
                {"item": "Pressure Point"}, {"item": "Cryotic"}, {"item": "Grokdrul"},
            ]
        }), "B")

    def test_pool_of_drops_needs_two_hits(self):
        self.assertEqual(br.pool_of_drops({
            "rewardPoolDrops": [{"item": "Redirection"}]
        }), "")

    def test_current_pool_prefers_unique_name(self):
        syndicate = {
            "jobs": [
                {"uniqueName": "TierATableBRewards",
                 "rewardPoolDrops": [{"item": "Redirection"}, {"item": "Oxium"}]},
            ]
        }
        self.assertEqual(br.current_pool(syndicate), "B")

    def test_current_pool_without_jobs(self):
        self.assertEqual(br.current_pool({}), "")
        self.assertEqual(br.current_pool(None), "")
        self.assertEqual(br.current_pool({"rotation": "c"}), "C")

    def test_next_pool_cycles(self):
        self.assertEqual(br.next_pool("A"), "B")
        self.assertEqual(br.next_pool("B"), "C")
        self.assertEqual(br.next_pool("C"), "A")
        self.assertEqual(br.next_pool("A", 2), "C")
        self.assertEqual(br.next_pool(""), "")

    def test_level_key(self):
        self.assertEqual(br.level_key([5, 15]), "5-15")
        self.assertEqual(br.level_key([100, 100]), "100-100")
        self.assertEqual(br.level_key([]), "")

    def test_pool_rewards(self):
        rewards = br.pool_rewards("5-15", "A")
        self.assertIn("弹道偏转", rewards)
        self.assertIn("琉璃仕女机体蓝图", rewards)
        self.assertEqual(br.pool_rewards("5-15", "Z"), [])

    def test_signature_rewards(self):
        sig_a = br.signature_rewards("A")
        sig_b = br.signature_rewards("B")
        self.assertTrue(sig_a)
        self.assertTrue(sig_b)
        # 两池标志应不同（取的是本池独有奖励）
        self.assertFalse(set(sig_a) & set(sig_b))
        self.assertEqual(br.signature_rewards(""), [])


if __name__ == "__main__":
    unittest.main()
