import json
import unittest
from test_llm_any_url import Provider
import llm


class ReasoningTests(unittest.TestCase):
    def setUp(self):
        self.provider = Provider()
        self.addCleanup(self.provider.close)
        self.msg = [{'role': 'user', 'content': 'test'}]
        self.client = llm.Client(api_key='fake-key', base_url=self.provider.url + '/chat/completions', retries=0)

    def serve(self, chunks):
        data = ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
        self.provider.routes['/chat/completions'] = lambda h, b: (200, 'text/event-stream', data)

    def test_encrypted_then_content_not_exposed(self):
        self.serve([{'choices': [{'delta': {'reasoning_details': [{'type': 'reasoning.encrypted', 'data': 'SECRET-BLOB'}]}}]}, {'choices': [{'delta': {'content': 'hello'}, 'finish_reason': 'stop'}]}])
        thoughts = []
        self.assertEqual(self.client.chat(self.msg, on_thought=thoughts.append)['text'], 'hello')
        self.assertEqual(thoughts, [])

    def test_text_details(self):
        self.serve([{'choices': [{'delta': {'reasoning_details': [{'type': 'reasoning.text', 'text': 'step'}], 'content': 'yes'}, 'finish_reason': 'stop'}]}])
        thoughts = []
        self.client.chat(self.msg, on_thought=thoughts.append)
        self.assertEqual(thoughts, ['step'])

    def test_empty_length_explains_and_redacts(self):
        self.serve([{'choices': [{'delta': {'reasoning_details': [{'type': 'reasoning.encrypted', 'data': 'SECRET-BLOB'}]}, 'finish_reason': 'length'}]}])
        with self.assertRaises(llm.LLMError) as ctx:
            self.client.chat(self.msg, effort='high')
        self.assertIn('бюджет токенов', str(ctx.exception))
        self.assertNotIn('SECRET-BLOB', str(ctx.exception))

    def test_empty_encrypted(self):
        self.serve([{'choices': [{'delta': {'reasoning_details': [{'type': 'reasoning.encrypted', 'data': 'SECRET-BLOB'}]}, 'finish_reason': 'stop'}]}])
        with self.assertRaises(llm.LLMError) as ctx:
            self.client.chat(self.msg)
        self.assertIn('зашифрованное', str(ctx.exception))
        self.assertNotIn('SECRET-BLOB', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
