import io
import threading
import unittest
from unittest import mock
import urllib.error

import llm
import orchestrator


class FakeStream:
    def __enter__(self):
        return iter([b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n', b'data: [DONE]\n'])

    def __exit__(self, typ, val, tb):
        return False


class AgentFixTests(unittest.TestCase):
    def test_retry_429(self):
        err = urllib.error.HTTPError('http://example', 429, 'limited', {'Retry-After': '75'}, io.BytesIO(b'limit'))
        c = llm.Client(api_key='test', retries=1)
        with mock.patch('llm.urllib.request.urlopen', side_effect=[err, FakeStream()]) as call:
            with mock.patch.object(c, '_wait_cooldown'):
                result = c.chat([{'role': 'user', 'content': 'test'}])
        self.assertEqual(result['text'], 'ok')
        self.assertEqual(call.call_count, 2)
        self.assertGreater(c.cooldown_until, 0)
        self.assertEqual(llm._backoff(0, '75'), 75)

    def test_shared_wait_can_be_cancelled(self):
        stop = threading.Event()
        stop.set()
        client = llm.Client(api_key='test', stop_event=stop)
        client.cooldown_until = llm.time.monotonic() + 60
        with self.assertRaises(llm.LLMError):
            client._wait_cooldown()

    def test_partial_failures_are_not_success(self):
        manager = orchestrator.Manager()
        rt = orchestrator.Runtime('test')
        manager.runtimes['test'] = rt
        with mock.patch.object(orchestrator.db, 'list_agents', return_value=[]):
            with mock.patch.object(orchestrator, 'attached_files', return_value=[]):
                with mock.patch.object(manager, '_run_main'):
                    with mock.patch.object(orchestrator, '_tally', return_value=(60, 40, 100)):
                        with mock.patch.object(orchestrator.db, 'update_project') as update:
                            with mock.patch.object(orchestrator, 'emit'):
                                with mock.patch.object(orchestrator.bus, 'publish'):
                                    manager._run_project(rt, {'concurrency': 1})
        self.assertEqual(update.call_args.kwargs['status'], 'error')
        self.assertEqual(update.call_args.kwargs['progress'], .6)


if __name__ == '__main__':
    unittest.main()
