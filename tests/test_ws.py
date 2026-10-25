import socket
import struct
import threading
import unittest

from bot import ws


class AcceptKeyTest(unittest.TestCase):
    def test_rfc6455_example(self):
        # RFC 6455 第 1.3 节给出的示例
        self.assertEqual(
            ws.accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
            "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=",
        )


class FrameTest(unittest.TestCase):
    def _decode_header(self, frame):
        first, second = frame[0], frame[1]
        masked = bool(second & 0x80)
        length = second & 0x7F
        offset = 2
        if length == 126:
            length = struct.unpack("!H", frame[2:4])[0]
            offset = 4
        elif length == 127:
            length = struct.unpack("!Q", frame[2:10])[0]
            offset = 10
        key = frame[offset:offset + 4] if masked else b""
        if masked:
            offset += 4
        return {
            "fin": bool(first & 0x80),
            "opcode": first & 0x0F,
            "masked": masked,
            "length": length,
            "payload": frame[offset:],
            "key": key,
        }

    def test_length_boundaries(self):
        for size in (0, 1, 125, 126, 65535, 65536, 70000):
            frame = ws.encode_frame(ws.OP_TEXT, b"a" * size, mask=True)
            info = self._decode_header(frame)
            self.assertEqual(info["length"], size)
            self.assertEqual(info["opcode"], ws.OP_TEXT)
            self.assertTrue(info["masked"])
            self.assertEqual(len(info["payload"]), size)

    def test_unmasked_server_frame(self):
        frame = ws.encode_frame(ws.OP_TEXT, b"hello", mask=False)
        info = self._decode_header(frame)
        self.assertFalse(info["masked"])
        self.assertEqual(info["payload"], b"hello")

    def test_mask_roundtrip(self):
        key = b"\x01\x02\x03\x04"
        masked = ws._xor_mask(b"abcdef", key)
        self.assertEqual(ws._xor_mask(masked, key), b"abcdef")


class SocketPairTest(unittest.TestCase):
    def setUp(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        self.client = ws.WebSocket(left, client_side=True)
        self.server = ws.WebSocket(right, client_side=False)

    def test_text_roundtrip(self):
        self.client.send_text("你好，Tenno")
        self.assertEqual(self.server.recv_text(timeout=2), "你好，Tenno")

    def test_binary_roundtrip(self):
        self.client.send_bytes(b"\x00\x01\x02", opcode=ws.OP_BINARY)
        opcode, payload = self.server.recv_message(timeout=2)
        self.assertEqual(opcode, ws.OP_BINARY)
        self.assertEqual(payload, b"\x00\x01\x02")

    def test_fragmented_message(self):
        # 手工发送分片：首帧 fin=0 + 续帧 fin=1
        self.client.sock.sendall(ws.encode_frame(ws.OP_TEXT, b"hel", fin=False))
        self.client.sock.sendall(ws.encode_frame(ws.OP_CONT, b"lo ", fin=False))
        self.client.sock.sendall(ws.encode_frame(ws.OP_CONT, b"world", fin=True))
        self.assertEqual(self.server.recv_text(timeout=2), "hello world")

    def test_ping_gets_pong(self):
        # 服务端发 ping 后再发一条文本；客户端读消息时应先回 pong
        self.server.send_ping(b"hb")
        self.server.send_text("hi")
        self.assertEqual(self.client.recv_text(timeout=2), "hi")
        fin, opcode, payload = self.server.recv_frame()
        self.assertTrue(fin)
        self.assertEqual(opcode, ws.OP_PONG)
        self.assertEqual(payload, b"hb")

    def test_close_frame(self):
        self.client.close(1000, "bye")
        with self.assertRaises(ws.ConnectionClosed) as ctx:
            self.server.recv_message(timeout=2)
        self.assertEqual(ctx.exception.code, 1000)

    def test_idle_timeout_is_not_disconnect(self):
        # 静默期读超时是正常的，不能误判成断线（否则机器人会每秒重连）
        with self.assertRaises(socket.timeout):
            self.server.recv_message(timeout=0.2)


class HandshakeTest(unittest.TestCase):
    def test_connect_and_authorization(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        received = {}

        def server():
            conn, _ = listener.accept()
            try:
                server_ws = ws.accept(conn, timeout=5)
                received["text"] = server_ws.recv_text(timeout=5)
                server_ws.send_text("pong")
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

        thread = threading.Thread(target=server, daemon=True)
        thread.start()
        client = ws.connect(
            "ws://127.0.0.1:%d/ws" % port,
            headers={"Authorization": "Bearer secret-token"},
            timeout=5,
        )
        try:
            client.send_text("ping")
            self.assertEqual(client.recv_text(timeout=5), "pong")
        finally:
            client.close()
            listener.close()
        thread.join(timeout=5)
        self.assertEqual(received.get("text"), "ping")


if __name__ == "__main__":
    unittest.main()

