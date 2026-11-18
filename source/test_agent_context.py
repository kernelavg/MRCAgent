import json
import unittest
from unittest import mock

import db
import orchestrator
import tools


class CapturingClient:
    def __init__(self):
        self.messages = []

    def chat(self, messages, effort=None, max_tokens=None, on_token=None, on_thought=None, on_reason=None):
        self.messages.append(json.loads(json.dumps(messages, ensure_ascii=False)))
        if len(self.messages) == 1:
            text = '```action\n{"tool":"read_file","path":"example.txt"}\n```'
        else:
            text = 'готово'
        return {'text': text, 'usage': {'in': 2, 'out': 3}, 'truncated': False}


class AgentContextTests(unittest.TestCase):
    def setUp(self):
        self.project = db.create_project('context-owner', 'context', 'длинная цель')['id']
        self.aid = db.add_agent(self.project, 1, 'Получатель', 'Программист', 'code', '', 'fake')
        self.bid = db.add_agent(self.project, 2, 'Отправитель', 'Программист', 'code', '', 'fake')
        self.agents = db.list_agents(self.project)
        self.agent = next(a for a in self.agents if a['id'] == self.aid)
        self.sender = next(a for a in self.agents if a['id'] == self.bid)
        self.addCleanup(lambda: tools.drop_workspace(self.project))
        self.addCleanup(lambda: db.delete_project(self.project))

    def test_full_plan_files_messages_and_history(self):
        plan = ''.join('план ' for n in range(1400)) + 'КОНЕЦ_ПЛАНА'
        db.update_project(self.project, brief=plan)
        for n in range(55):
            tools.write_file(self.project, 'file%02d.txt' % n, 'x', agent_id=self.aid)
        msg = ''.join('письмо ' for n in range(500)) + 'КОНЕЦ_СООБЩЕНИЯ'
        self.assertTrue(orchestrator._send_msg(self.project, self.sender, 'Отправитель', 'Получатель', msg).startswith('отправлено'))
        for n in range(25):
            db.add_agent_msg(self.project, self.bid, self.aid, 'сообщение-%s' % n)
        rt = orchestrator.Runtime(self.project)
        rt.client = CapturingClient()
        manager = orchestrator.Manager()
        with mock.patch.object(manager, '_exec_action', return_value='результат инструмента'):
            self.assertEqual(manager._real_agent(rt, self.agent, {'loop_guard': 0}, 'задача'), 'готово')
        first, second = rt.client.messages
        prompt = first[0]['content']
        self.assertIn('КОНЕЦ_ПЛАНА', prompt)
        self.assertIn('file54.txt', prompt)
        inbox = first[-1]['content']
        self.assertIn('КОНЕЦ_СООБЩЕНИЯ', inbox)
        self.assertIn('сообщение-24', inbox)
        self.assertIn(first[-1], second)
        self.assertIn('результат инструмента', second[-1]['content'])

    def test_attachment_list_and_digest_are_not_truncated(self):
        digest = ''.join('текст' for n in range(300)) + 'КОНЕЦ'
        rows = [{'name': 'файл%d.txt' % n, 'rel': 'files/file%d' % n, 'kind': 'text', 'size': 10, 'digest': digest} for n in range(15)]
        with mock.patch.object(orchestrator, 'attached_files', return_value=rows):
            text = orchestrator._attach_prompt(self.project)
        self.assertIn('@файл14.txt', text)
        self.assertEqual(text.count('КОНЕЦ'), 15)

    def test_planner_saves_full_plan(self):
        lead = db.add_agent(self.project, 3, 'Планировщик', 'Планировщик', 'lead', '', 'fake')
        lead_row = next(a for a in db.list_agents(self.project) if a['id'] == lead)
        plan = ''.join('подробно ' for n in range(800)) + 'КОНЕЦ'
        rt = orchestrator.Runtime(self.project)
        rt.client = CapturingClient()
        def answer(messages, effort=None, max_tokens=None, on_token=None, on_thought=None, on_reason=None):
            return {'text': plan, 'usage': {'in': 1, 'out': 1}, 'truncated': False}
        rt.client.chat = answer
        orchestrator.Manager()._real_agent(rt, lead_row, {'loop_guard': 0}, 'план')
        self.assertEqual(db.get_project(self.project)['brief'], plan)


if __name__ == '__main__':
    unittest.main()
