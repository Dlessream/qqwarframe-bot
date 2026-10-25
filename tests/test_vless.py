import importlib.util
import os
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE_PATH = os.path.join(BASE, "deploy", "vless_to_xray.py")

spec = importlib.util.spec_from_file_location("vless_to_xray", MODULE_PATH)
vless = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vless)

REALITY = (
    "vless://11111111-1111-1111-1111-111111111111@1.2.3.4:443"
    "?encryption=none&flow=xtls-rprx-vision&security=reality"
    "&sni=www.example.com&fp=chrome&pbk=PUBLIC_KEY&sid=deadbeef"
    "&type=tcp&headerType=none#demo"
)
WS_TLS = (
    "vless://22222222-2222-2222-2222-222222222222@example.com:8443"
    "?security=tls&type=ws&path=%2Fws&host=cdn.example.com"
    "&sni=cdn.example.com&fp=firefox&alpn=h2%2Chttp%2F1.1#ws"
)


class ParseTest(unittest.TestCase):
    def test_reality(self):
        config = vless.parse_vless(REALITY)
        inbound = config["inbounds"][0]
        self.assertEqual(inbound["port"], 10809)
        self.assertEqual(config["inbounds"][1]["port"], 10808)

        outbound = config["outbounds"][0]
        vnext = outbound["settings"]["vnext"][0]
        self.assertEqual(vnext["address"], "1.2.3.4")
        self.assertEqual(vnext["port"], 443)
        user = vnext["users"][0]
        self.assertEqual(user["id"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(user["flow"], "xtls-rprx-vision")
        self.assertEqual(user["encryption"], "none")

        stream = outbound["streamSettings"]
        self.assertEqual(stream["network"], "tcp")
        self.assertEqual(stream["security"], "reality")
        reality = stream["realitySettings"]
        self.assertEqual(reality["serverName"], "www.example.com")
        self.assertEqual(reality["fingerprint"], "chrome")
        self.assertEqual(reality["publicKey"], "PUBLIC_KEY")
        self.assertEqual(reality["shortId"], "deadbeef")

    def test_ws_tls(self):
        config = vless.parse_vless(WS_TLS)
        stream = config["outbounds"][0]["streamSettings"]
        self.assertEqual(stream["network"], "ws")
        self.assertEqual(stream["security"], "tls")
        self.assertEqual(stream["tlsSettings"]["serverName"], "cdn.example.com")
        self.assertEqual(stream["tlsSettings"]["alpn"], ["h2", "http/1.1"])
        self.assertEqual(stream["wsSettings"]["path"], "/ws")
        self.assertEqual(stream["wsSettings"]["headers"]["Host"], "cdn.example.com")

    def test_custom_ports(self):
        config = vless.parse_vless(REALITY, http_port=20809, socks_port=20808,
                                   listen="127.0.0.1")
        self.assertEqual(config["inbounds"][0]["port"], 20809)
        self.assertEqual(config["inbounds"][0]["listen"], "127.0.0.1")

    def test_private_ip_direct(self):
        config = vless.parse_vless(REALITY)
        self.assertEqual(config["routing"]["rules"][0]["outboundTag"], "direct")
        self.assertIn("192.168.0.0/16", config["routing"]["rules"][0]["ip"])

    def test_missing_public_key(self):
        with self.assertRaises(ValueError):
            vless.parse_vless(REALITY.replace("&pbk=PUBLIC_KEY", ""))

    def test_missing_sni(self):
        with self.assertRaises(ValueError):
            vless.parse_vless(REALITY.replace("&sni=www.example.com", ""))

    def test_not_vless(self):
        with self.assertRaises(ValueError):
            vless.parse_vless("vmess://xxx")

    def test_config_is_json_serializable(self):
        import json
        text = json.dumps(vless.parse_vless(REALITY))
        self.assertIn("realitySettings", text)


if __name__ == "__main__":
    unittest.main()
