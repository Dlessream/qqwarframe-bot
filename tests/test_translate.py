import unittest

from bot.translate import TranslateError, Translator


class FakeGoogle:
    def __init__(self):
        self.urls = []

    def get_json(self, url, headers=None):
        self.urls.append(url)
        return [[["这就是伏特。", "This is Volt.", None]], None, "en"]


class FakeMyMemory:
    def get_json(self, url, headers=None):
        if "translate.googleapis" in url:
            raise TranslateError("blocked")
        return {"responseData": {"translatedText": "这是伏特。"}}

class TranslatorTest(unittest.TestCase):
    def test_google_translation(self):
        fake = FakeGoogle()
        translator = Translator({"proxy": "none", "translate_seconds": 10},
                                cache_config={"enabled": False}, http=fake)
        self.assertEqual(translator.translate("This is Volt."), "这就是伏特。")
        self.assertIn("translate.googleapis", fake.urls[0])

    def test_mymemory_fallback(self):
        translator = Translator({"proxy": "none", "translate_seconds": 10},
                                cache_config={"enabled": False},
                                http=FakeMyMemory())
        self.assertEqual(translator.translate("This is Volt."), "这是伏特。")

    def test_empty(self):
        translator = Translator({"proxy": "none"}, http=FakeGoogle())
        self.assertEqual(translator.translate(""), "")


if __name__ == "__main__":
    unittest.main()
