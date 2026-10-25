import unittest

from bot.config import load_config
from bot.net import HttpError
from bot.wiki import BWikiClient, WikiClient, wiki_proxy_candidates


class FailJson:
    def get_json(self, *args, **kwargs):
        raise HttpError("403", 403)


class OkJson:
    def get_json(self, *args, **kwargs):
        return {"query": {"pages": {}}}


class FailText:
    def get_text(self, *args, **kwargs):
        raise HttpError("403", 403)


class OkText:
    def get_text(self, *args, **kwargs):
        return "<html>ok</html>"


class WikiProxyTest(unittest.TestCase):
    def setUp(self):
        self.config = load_config("config.example.json")["wiki"]
        self.config["fallback_bwiki"] = False

    def test_proxy_modes(self):
        cfg = dict(self.config)
        cfg["proxy_mode"] = "direct"
        self.assertEqual(wiki_proxy_candidates(cfg), ["none"])
        cfg["proxy_mode"] = "system"
        self.assertEqual(wiki_proxy_candidates(cfg), [""])
        cfg["proxy_mode"] = "custom"
        cfg["proxy"] = "http://127.0.0.1:7890"
        self.assertEqual(wiki_proxy_candidates(cfg), ["http://127.0.0.1:7890"])
        cfg["proxy_mode"] = "auto"
        cfg["proxy_fallbacks"] = ["none"]
        self.assertEqual(wiki_proxy_candidates(cfg),
                         ["http://127.0.0.1:7890", "none"])

    def test_wiki_api_falls_back(self):
        client = WikiClient(self.config, http=FailJson())
        client._http_clients = [FailJson(), OkJson()]
        client.http = client._http_clients[0]
        self.assertEqual(client._call({}), {"query": {"pages": {}}})

    def test_bwiki_falls_back(self):
        client = BWikiClient(self.config)
        client._http_clients = [FailText(), OkText()]
        client.http = client._http_clients[0]
        self.assertEqual(client._get("http://example.test"), "<html>ok</html>")


if __name__ == "__main__":
    unittest.main()
