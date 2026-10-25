import json
import os
import unittest
from urllib.parse import parse_qs, urlparse

from bot.wiki import (BWikiClient, WikiClient, WikiError, WikiHit, WikiPage,
                      clean_wiki_text, extract_bwiki_intro, extract_bwiki_title,
                      parse_bwiki_search)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(BASE, "tests", "fixtures")


def load_fixture(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as handle:
        return json.load(handle)


class FakeHttp:
    def __init__(self, extract=None, search=None, parse=None):
        self.extract = extract if extract is not None else load_fixture("wiki_extract.json")
        self.search = search if search is not None else load_fixture("wiki_search.json")
        self.parse = parse if parse is not None else load_fixture("wiki_parse.json")
        self.urls = []

    def get_json(self, url, headers=None):
        self.urls.append(url)
        query = parse_qs(urlparse(url).query)
        action = (query.get("action") or [""])[0]
        if action == "parse":
            return self.parse
        if (query.get("list") or [""])[0] == "search":
            return self.search
        if (query.get("prop") or [""])[0] == "extracts":
            return self.extract
        raise AssertionError("unexpected wiki request: %s" % url)


def make_client(http, **overrides):
    config = {
        "api_url": "https://warframe.huijiwiki.com/api.php",
        "site_url": "https://warframe.huijiwiki.com/wiki/",
        "summary_chars": 280,
        "timeout_seconds": 5,
        "retries": 0,
        "proxy": "",
        "fallback_bwiki": False,
    }
    config.update(overrides)
    return WikiClient(config, http=http)


class CleanTextTest(unittest.TestCase):
    def test_strips_html_and_entities(self):
        raw = '<span class="searchmatch">高斯</span>&amp;电池\u2009充能'
        self.assertEqual(clean_wiki_text(raw), "高斯&电池 充能")

    def test_collapses_blank_lines(self):
        self.assertEqual(clean_wiki_text("a\n\n\n\nb"), "a\n\nb")

    def test_truncates_at_sentence(self):
        text = "第一句话。" + "填充" * 100
        result = clean_wiki_text(text, 40)
        self.assertTrue(result.endswith("…"))
        self.assertLessEqual(len(result), 41)


class WikiSearchTest(unittest.TestCase):
    def test_search_parses_hits(self):
        client = make_client(FakeHttp())
        hits = client.search("高斯", limit=3)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0].title, "高斯")
        self.assertEqual(hits[0].url, "https://warframe.huijiwiki.com/wiki/高斯")
        self.assertNotIn("<span", hits[0].snippet)

    def test_search_limit_capped(self):
        client = make_client(FakeHttp())
        client.search("高斯", limit=99)
        query = parse_qs(urlparse(client.http.urls[0]).query)
        self.assertEqual(query["srlimit"], ["10"])


class WikiPageTest(unittest.TestCase):
    def test_page_returns_extract(self):
        client = make_client(FakeHttp())
        page = client.page("Excalibur")
        self.assertIsNotNone(page)
        self.assertEqual(page.title, "Excalibur")
        self.assertIn("以剑为主题", page.extract)
        self.assertIn("Excalibur", page.url)

    def test_missing_page_returns_none(self):
        payload = {"query": {"pages": {"-1": {"title": "不存在", "missing": ""}}}}
        client = make_client(FakeHttp(extract=payload))
        self.assertIsNone(client.page("不存在"))

    def test_falls_back_to_parse_when_no_extract(self):
        empty = {"query": {"pages": {"1": {"title": "高斯", "pageid": 1}}}}
        client = make_client(FakeHttp(extract=empty))
        page = client.page("高斯")
        self.assertIsNotNone(page)
        self.assertIn("速度为主题", page.extract)
        self.assertTrue(any("action=parse" in url for url in client.http.urls))

    def test_extract_truncated_to_config(self):
        long_text = "很长的介绍。" * 50
        payload = {"query": {"pages": {"1": {"title": "高斯", "extract": long_text}}}}
        client = make_client(FakeHttp(extract=payload), summary_chars=80)
        page = client.page("高斯")
        self.assertLessEqual(len(page.extract), 82)

    def test_api_error_raises(self):
        client = make_client(FakeHttp(extract={"error": {"info": "boom"}}))
        with self.assertRaises(WikiError):
            client.page("x")


class WikiLookupTest(unittest.TestCase):
    def test_exact_title(self):
        client = make_client(FakeHttp())
        page, hits = client.lookup("Excalibur")
        self.assertIsNotNone(page)
        self.assertEqual(hits, [])

    def test_search_then_first_page(self):
        missing = {"query": {"pages": {"-1": {"title": "高", "missing": ""}}}}
        extract = {"query": {"pages": {"7100": {
            "pageid": 7100, "title": "高斯",
            "extract": "高斯是一款以速度为主题的战甲。"}}}}

        class SequenceHttp(FakeHttp):
            def __init__(self):
                super().__init__()
                self.extract_calls = 0

            def get_json(self, url, headers=None):
                query = parse_qs(urlparse(url).query)
                self.urls.append(url)
                if (query.get("prop") or [""])[0] == "extracts":
                    self.extract_calls += 1
                    return missing if self.extract_calls == 1 else extract
                if (query.get("list") or [""])[0] == "search":
                    return self.search
                raise AssertionError("unexpected wiki request: %s" % url)

        http = SequenceHttp()
        client = make_client(http)
        page, hits = client.lookup("高", limit=3)
        self.assertIsNotNone(page)
        self.assertEqual(page.title, "高斯")
        self.assertTrue(hits)
        self.assertEqual(http.extract_calls, 2)

    def test_no_result(self):
        empty = {"query": {"searchinfo": {"totalhits": 0}, "search": []}}
        missing = {"query": {"pages": {"-1": {"title": "x", "missing": ""}}}}
        client = make_client(FakeHttp(extract=missing, search=empty))
        page, hits = client.lookup("zzzz")
        self.assertIsNone(page)
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()

class BWikiParserTest(unittest.TestCase):
    HTML = (
        '<html><head><title>高斯 - Warframe - 灰机wiki</title></head><body>'
        '<h1 id="firstHeading">高斯</h1>'
        '<div class="mw-parser-output">'
        '<p>高斯是一款以速度为主题的战甲。</p>'
        '<p>他的技能围绕速度与电能展开。</p>'
        '<table><tr><td>忽略表格</td></tr></table>'
        '</div></body></html>'
    )
    SEARCH = (
        '<div class="mw-search-result-heading"><a href="/warframe/%E9%AB%98%E6%96%AF" '
        'title="高斯">高斯</a></div>'
        '<div class="mw-search-result-heading"><a href="/warframe/%E9%AB%98%E6%96%AF/%E6%8A%80%E8%83%BD" '
        'title="高斯/技能">高斯/技能</a></div>'
        '<a href="/warframe/%E7%89%B9%E6%AE%8A:%E6%90%9C%E7%B4%A2" title="特殊:搜索">特殊</a>'
    )

    def test_title(self):
        self.assertEqual(extract_bwiki_title(self.HTML), "高斯")

    def test_intro(self):
        text = extract_bwiki_intro(self.HTML, 100)
        self.assertIn("速度为主题", text)
        self.assertNotIn("忽略表格", text)

    def test_search(self):
        hits = parse_bwiki_search(self.SEARCH, "https://wiki.biligame.com/warframe")
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0].title, "高斯")
        self.assertTrue(hits[0].url.startswith("https://wiki.biligame.com/warframe/"))

    def test_client_page(self):
        class TextHttp:
            def get_text(self, url, headers=None):
                return BWikiParserTest.HTML
        client = BWikiClient({"summary_chars": 100, "proxy": "none"})
        client.http = TextHttp()
        page = client.page("高斯")
        self.assertIsNotNone(page)
        self.assertEqual(page.title, "高斯")
        self.assertIn("速度为主题", page.extract)

    def test_client_missing(self):
        class TextHttp:
            def get_text(self, url, headers=None):
                return "<html><body>此页面目前没有内容</body></html>"
        client = BWikiClient({"proxy": "none"})
        client.http = TextHttp()
        self.assertIsNone(client.page("不存在"))


class BWikiFallbackTest(unittest.TestCase):
    def test_fallback_on_huijiwiki_error(self):
        payload = {"error": {"info": "blocked"}}
        client = make_client(FakeHttp(extract=payload))
        client.bwiki = FakeBWiki(WikiPage("高斯", "来自 BWIKI 的摘要", "https://bwiki/高斯"))
        page, hits = client.lookup("高斯")
        self.assertIsNotNone(page)
        self.assertEqual(page.title, "高斯")
        self.assertIn("BWIKI", page.extract)

    def test_fallback_when_primary_empty(self):
        missing = {"query": {"pages": {"-1": {"title": "x", "missing": ""}}}}
        empty = {"query": {"searchinfo": {"totalhits": 0}, "search": []}}
        client = make_client(FakeHttp(extract=missing, search=empty))
        client.bwiki = FakeBWiki(WikiPage("高斯", "兜底摘要", "https://bwiki/高斯"),
                                 [WikiHit("高斯", "", 1, "https://bwiki/高斯")])
        page, hits = client.lookup("高斯")
        self.assertIsNotNone(page)
        self.assertTrue(hits)

    def test_fallback_error_reraises_primary(self):
        payload = {"error": {"info": "blocked"}}
        client = make_client(FakeHttp(extract=payload))
        client.bwiki = FakeBWiki()
        with self.assertRaises(WikiError):
            client.lookup("高斯")


class FakeBWiki:
    def __init__(self, page=None, hits=None):
        self.page = page
        self.hits = hits or []

    def lookup(self, query, limit=3):
        return self.page, self.hits


class BWikiTitleGuardTest(unittest.TestCase):
    def test_reject_redirect_to_homepage(self):
        class TextHttp:
            def get_text(self, url, headers=None):
                return ('<html><body><h1 id="firstHeading">首页</h1>'
                        '<div class="mw-parser-output"><p>BWIKI是bilibili游戏旗下'
                        '的WIKI平台，所有WIKI均来自用户申请与维护，这里有很多内容。</p>'
                        '</div></body></html>')
        client = BWikiClient({"summary_chars": 100, "proxy": "none"})
        client.http = TextHttp()
        self.assertIsNone(client.page("高斯"))

