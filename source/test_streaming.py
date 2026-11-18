"""Проверка реального разбора потоковых ответов и публичных рассуждений."""
import json
import unittest
from unittest.mock import patch
import llm
import bus
import server


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        events = [
            {"choices": [{"delta": {"reasoning_content": "Раз"}}]},
            {"choices": [{"delta": {"reasoning": "бор"}}]},
            {"choices": [{"delta": {"content": "При"}}]},
            {"choices": [{"delta": {"content": "вет"}, "finish_reason": "stop"}]},
        ]
        return iter([("data: " + json.dumps(item) + "\n\n").encode() for item in events] + [b"data: [DONE]\n\n"])


class StreamTest(unittest.TestCase):
    def test_chunks_and_reasoning(self):
        seen = []
        reason = []
        requests = []
        def fake_open(req, timeout=None):
            requests.append(json.loads(req.data))
            return FakeResponse()
        def on_token(piece, total):
            seen.append((piece, total))
        with patch("urllib.request.urlopen", side_effect=fake_open):
            result = llm.Client(api_key="key", retries=0).chat(
                [{"role": "user", "content": "hello"}], effort="high",
                on_token=on_token, on_thought=reason.append)
        self.assertEqual(result["text"], "Привет")
        self.assertEqual(seen, [("При", 3), ("вет", 6)])
        self.assertEqual(reason, ["Раз", "бор"])
        self.assertEqual(requests[0]["reasoning_effort"], "high")
        self.assertTrue(requests[0]["stream"])

    def test_live_snapshot_and_completion(self):
        chat_id = 'snapshot-test'
        server.CHAT_LIVE[chat_id] = {'text': '', 'thought': '', 'busy': True, 'seq': 0}
        try:
            server.chat_event(chat_id, {'type': 'token', 'text': 'Первая часть'})
            first = server.chat_snapshot(chat_id)
            self.assertEqual(first['text'], 'Первая часть')
            self.assertEqual(first['seq'], 1)
            server.chat_event(chat_id, {'type': 'token', 'text': 'Первая часть и вторая'})
            self.assertEqual(server.chat_snapshot(chat_id)['seq'], 2)
            server.chat_event(chat_id, {'type': 'done', 'text': 'Первая часть и вторая'})
            self.assertIsNone(server.chat_snapshot(chat_id))
        finally:
            server.CHAT_LIVE.pop(chat_id, None)

    def test_demo_emits_cumulative_snapshots(self):
        sub = bus.subscribe()
        try:
            with patch.object(server, "setting_llm_client") as factory, \
                 patch.object(server.db, "chat_messages", return_value=[]), \
                 patch.object(server.db, "add_message"), \
                 patch.object(server.time, "sleep"):
                factory.return_value.ready = False
                server.answer_chat("test-chat", "запрос", "user")
            events = []
            while not sub.q.empty():
                events.append(sub.q.get_nowait()["payload"])
            chunks = [event["text"] for event in events if event["type"] == "token"]
            self.assertGreater(len(chunks), 1)
            self.assertTrue(all(b.startswith(a) for a, b in zip(chunks, chunks[1:])))
            self.assertEqual(chunks[-1], events[-1]["text"])
        finally:
            bus.unsubscribe(sub)


if __name__ == "__main__":
    unittest.main()
