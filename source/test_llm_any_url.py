"""Интеграционные тесты клиента и проверки провайдера на локальных сокетах."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import llm


class Provider:
    def __init__(self):
        self.requests = []
        self.routes = {}
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                size = int(self.headers.get('Content-Length') or 0)
                try:
                    body = json.loads(self.rfile.read(size).decode('utf-8'))
                except ValueError:
                    body = {}
                provider.requests.append({'path': self.path, 'body': body,
                                          'authorization': self.headers.get('Authorization'),
                                          'key': self.headers.get('x-api-key')})
                handler = provider.routes.get(self.path)
                status, ctype, data = handler(self, body) if handler else (404, 'application/json', '{}')
                self.send_response(status)
                self.send_header('Content-Type', ctype)
                self.end_headers()
                self.wfile.write(data.encode('utf-8'))

            def log_request(self, code='-', size='-'):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:%s' % self.server.server_port

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def packet(value):
    return json.dumps(value, ensure_ascii=False)


def sse(value):
    return 200, 'text/event-stream', 'data: ' + packet(value) + '\n\ndata: [DONE]\n\n'


def success(handler, body):
    return sse({'model': 'test-model', 'choices': [{'delta': {'content': 'Привет'}, 'finish_reason': 'stop'}]})


class AnyUrlTests(unittest.TestCase):
    def setUp(self):
        self.provider = Provider()
        self.addCleanup(self.provider.close)
        self.msg = [{'role': 'user', 'content': 'hi'}]

    def client(self, path):
        return llm.Client(api_key='fake-key', base_url=self.provider.url + path, model='test-model', retries=0)

    def test_01_fallback_and_cache(self):
        self.provider.routes['/v1/chat/completions'] = success
        client = self.client('/v1')
        self.assertEqual(client.chat(self.msg)['text'], 'Привет')
        self.assertEqual(client.chat(self.msg)['text'], 'Привет')
        self.assertEqual([x['path'] for x in self.provider.requests], ['/v1', '/v1/chat/completions', '/v1/chat/completions'])

    def test_02_complete_url_not_doubled(self):
        self.provider.routes['/v1/chat/completions'] = success
        self.assertEqual(self.client('/v1/chat/completions').chat(self.msg)['text'], 'Привет')
        self.assertEqual([x['path'] for x in self.provider.requests], ['/v1/chat/completions'])

    def test_03_arbitrary_path_first(self):
        self.provider.routes['/api/generate'] = success
        self.assertEqual(self.client('/api/generate').chat(self.msg)['text'], 'Привет')
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(self.provider.requests[0]['path'], '/api/generate')

    def test_04_anthropic_style(self):
        text = 'event: content_block_delta\ndata: ' + packet({'type': 'content_block_delta', 'delta': {'text': 'При'}}) + '\n\n'
        text += 'event: content_block_delta\ndata: ' + packet({'delta': {'text': 'вет'}}) + '\n\n'
        text += 'event: message_delta\ndata: ' + packet({'delta': {'stop_reason': 'max_tokens'}, 'usage': {'output_tokens': 7}}) + '\n\n'
        self.provider.routes['/anthropic'] = lambda h, b: (200, 'text/event-stream', text)
        result = self.client('/anthropic').chat(self.msg)
        self.assertEqual(result['text'], 'Привет')
        self.assertTrue(result['truncated'])
        self.assertEqual(result['usage']['out'], 7)

    def test_05_gemini_style(self):
        data = {'candidates': [{'content': {'parts': [{'text': 'Здравствуйте'}]}, 'finishReason': 'MAX_TOKENS'}],
                'usageMetadata': {'promptTokenCount': 8, 'candidatesTokenCount': 5}}
        self.provider.routes['/gemini'] = lambda h, b: sse(data)
        result = self.client('/gemini').chat(self.msg)
        self.assertEqual(result['text'], 'Здравствуйте')
        self.assertEqual(result['usage'], {'in': 8, 'out': 5})
        self.assertTrue(result['truncated'])

    def test_06_ndjson(self):
        data = packet({'response': 'Первая '}) + '\n' + packet({'response': 'часть'}) + '\n'
        self.provider.routes['/ndjson'] = lambda h, b: (200, 'application/x-ndjson', data)
        seen = []
        result = self.client('/ndjson').chat(self.msg, on_token=lambda piece, count: seen.append((piece, count)))
        self.assertEqual(result['text'], 'Первая часть')
        self.assertEqual(seen, [('Первая ', 7), ('часть', 12)])

    def test_07_non_stream_json(self):
        data = {'choices': [{'message': {'content': 'полный ответ'}, 'finish_reason': 'length'}],
                'usage': {'input_tokens': 20, 'output_tokens': 3}}
        self.provider.routes['/json'] = lambda h, b: (200, 'application/json', packet(data))
        result = self.client('/json').chat(self.msg)
        self.assertEqual(result['text'], 'полный ответ')
        self.assertTrue(result['truncated'])
        self.assertEqual(result['usage'], {'in': 20, 'out': 3})

    def test_08_unrecognised(self):
        data = packet({'weird': {'payload': 'привет'}})
        self.provider.routes['/unknown'] = lambda h, b: (200, 'application/json', data)
        with self.assertRaises(llm.LLMError) as ctx:
            self.client('/unknown').chat(self.msg)
        self.assertIn('не удалось распознать формат ответа', str(ctx.exception))
        self.assertIn('привет', str(ctx.exception))
        self.assertNotIn('Traceback', str(ctx.exception))

    def test_09_auth_fallback(self):
        def handler(h, body):
            if h.headers.get('Authorization'):
                return 401, 'application/json', '{}'
            return success(h, body)
        self.provider.routes['/auth'] = handler
        result = self.client('/auth').chat(self.msg)
        self.assertEqual(result['text'], 'Привет')
        self.assertEqual(self.provider.requests[0]['authorization'], 'Bearer fake-key')
        self.assertEqual(self.provider.requests[1]['key'], 'fake-key')
        self.assertIsNone(self.provider.requests[1]['authorization'])

    def test_10_both_auth_fail(self):
        self.provider.routes['/auth'] = lambda h, b: (401 if h.headers.get('Authorization') else 403, 'application/json', '{}')
        with self.assertRaises(llm.LLMError) as ctx:
            self.client('/auth').chat(self.msg)
        self.assertIn('401', str(ctx.exception))
        self.assertIn('403', str(ctx.exception))
        self.assertEqual(len(self.provider.requests), 2)

    def test_11_output_shape_and_text_paths(self):
        variants = [
            {'choices': [{'delta': {'content': 'ok'}}]},
            {'choices': [{'message': {'content': 'ok'}}]},
            {'delta': {'text': 'ok'}},
            {'content_block': {'delta': {'text': 'ok'}}},
            {'candidates': [{'content': {'parts': [{'text': 'ok'}]}}]},
            {'message': {'content': 'ok'}},
        ]
        for key in ('content', 'text', 'output', 'response', 'result', 'generated_text'):
            variants.append({key: 'ok'})
        self.provider.routes['/shape'] = lambda h, b: (200, 'application/json', packet(variants.pop(0)))
        expected = {'text', 'usage', 'latency', 'model', 'finish', 'truncated'}
        for _ in range(len(variants)):
            result = self.client('/shape').chat(self.msg)
            self.assertEqual(set(result), expected)
            self.assertEqual(set(result['usage']), {'in', 'out'})
            self.assertEqual(result['text'], 'ok')

    def test_both_missing_codes_and_urls(self):
        self.provider.routes['/missing/chat/completions'] = lambda h, b: (405, 'application/json', '{}')
        with self.assertRaises(llm.LLMError) as ctx:
            self.client('/missing').chat(self.msg)
        for val in ('404', '405', '/missing', '/missing/chat/completions'):
            self.assertIn(val, str(ctx.exception))

    def test_first_404_second_400_reports_both(self):
        self.provider.routes['/bad/chat/completions'] = lambda h, b: (400, 'application/json', '{}')
        with self.assertRaises(llm.LLMError) as ctx:
            self.client('/bad').chat(self.msg)
        for val in ('404', '400', '/bad', '/bad/chat/completions'):
            self.assertIn(val, str(ctx.exception))

    def test_http_error_does_not_echo_key(self):
        self.provider.routes['/leak'] = lambda h, b: (400, 'text/plain', 'bad fake-key')
        with self.assertRaises(llm.LLMError) as ctx:
            self.client('/leak').chat(self.msg)
        self.assertNotIn('fake-key', str(ctx.exception))

    def test_path_is_locked_per_response(self):
        data = 'data: ' + packet({'delta': {'text': 'A'}}) + '\n\n'
        data += 'data: ' + packet({'text': 'wrong', 'delta': {'text': 'B'}}) + '\n\n'
        self.provider.routes['/locked'] = lambda h, b: (200, 'text/event-stream', data)
        self.assertEqual(self.client('/locked').chat(self.msg)['text'], 'AB')


class ProviderEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='mrc-provider-test-')
        cls.provider = Provider()
        cls.provider.routes['/direct'] = lambda h, b: (200, 'application/json', packet({'model': 'unsaved-model', 'response': 'ок'}))
        cls.provider.routes['/reject'] = lambda h, b: (401, 'application/json', '{}')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cls.port = sock.getsockname()[1]
        env = dict(os.environ)
        env['MS_DATA'] = cls.temp.name
        env.pop('INCEPTION_API_KEY', None)
        root = Path(__file__).resolve().parent
        cls.proc = subprocess.Popen([sys.executable, str(root / 'server.py'), '--host', '127.0.0.1', '--port', str(cls.port), '--no-resume'],
                                    cwd=str(root), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.base = 'http://127.0.0.1:%s' % cls.port
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                urllib.request.urlopen(cls.base + '/api/health', timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError('сервер теста не запустился')
        status, result, headers = cls.request('/api/auth/register', {'name': 'Проверка', 'password': 'secure-test-password'})
        if status != 200 or not result.get('ok'):
            raise RuntimeError('регистрация теста не удалась')
        cls.cookie = headers.get('Set-Cookie').split(';', 1)[0]

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait(timeout=5)
        cls.provider.close()
        cls.temp.cleanup()

    @classmethod
    def request(cls, path, body=None, cookie=None, method='POST'):
        headers = {'Content-Type': 'application/json'}
        if cookie:
            headers['Cookie'] = cookie
        data = json.dumps(body).encode('utf-8') if body is not None else None
        req = urllib.request.Request(cls.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.status, json.loads(response.read().decode('utf-8')), response.headers
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode('utf-8')), exc.headers

    def test_12_success(self):
        status, value, _ = self.request('/api/settings/test_provider', {'api_key': 'fake-key', 'base_url': self.provider.url + '/direct', 'model': 'unsaved-model'}, self.cookie)
        self.assertEqual(status, 200)
        self.assertTrue(value['ok'])
        self.assertEqual(value['model'], 'unsaved-model')
        self.assertEqual(value['sample'], 'ок')
        self.assertIsInstance(value['latency'], (int, float))
        self.assertEqual(self.provider.requests[-1]['body']['stream'], False)
        self.assertEqual(self.provider.requests[-1]['body']['max_completion_tokens'], 10)

    def test_13_error_is_json_not_500(self):
        status, result, _ = self.request('/api/settings/test_provider', {'api_key': 'bad', 'base_url': self.provider.url + '/reject', 'model': 'x'}, self.cookie)
        self.assertEqual(status, 200)
        self.assertFalse(result['ok'])
        self.assertIn('401', result['error'])
        self.assertNotIn('Traceback', result['error'])

    def test_14_unauthenticated(self):
        status, result, _ = self.request('/api/settings/test_provider', {})
        self.assertEqual(status, 401)
        self.assertFalse(result['ok'])

    def test_saved_settings_fallback_and_non_admin_guard(self):
        settings = {'api_key': 'saved-secret', 'base_url': self.provider.url + '/direct', 'model': 'saved-model'}
        status, response, _ = self.request('/api/settings', settings, self.cookie, 'PUT')
        self.assertEqual(status, 200)
        self.assertTrue(response['ok'])
        status, result, _ = self.request('/api/settings/test_provider', {}, self.cookie)
        self.assertTrue(result['ok'])
        self.assertEqual(self.provider.requests[-1]['authorization'], 'Bearer saved-secret')
        status, _, headers = self.request('/api/auth/register', {'name': 'Другой', 'password': 'secure-test-password'})
        self.assertEqual(status, 200)
        other = headers.get('Set-Cookie').split(';', 1)[0]
        count = len(self.provider.requests)
        status, result, _ = self.request('/api/settings/test_provider', {'base_url': self.provider.url + '/reject'}, other)
        self.assertFalse(result['ok'])
        self.assertIn('администратор', result['error'])
        self.assertEqual(len(self.provider.requests), count)

    def test_15_unsaved_settings(self):
        before = self.request('/api/bootstrap', None, self.cookie, 'GET')[1]['settings']
        body = {'api_key': 'not-saved', 'base_url': self.provider.url + '/direct', 'model': 'temporary-model'}
        status, result, _ = self.request('/api/settings/test_provider', body, self.cookie)
        after = self.request('/api/bootstrap', None, self.cookie, 'GET')[1]['settings']
        self.assertTrue(result['ok'])
        self.assertEqual(self.provider.requests[-1]['body']['model'], 'temporary-model')
        self.assertEqual(self.provider.requests[-1]['authorization'], 'Bearer not-saved')
        self.assertEqual(before['base_url'], after['base_url'])
        self.assertEqual(before['model'], after['model'])
        self.assertEqual(before['has_key'], after['has_key'])


if __name__ == '__main__':
    unittest.main()
