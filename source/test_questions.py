import threading
import time
import unittest
from unittest.mock import patch
import db
import questions
import orchestrator


class QuestionTests(unittest.TestCase):
    def setUp(self):
        db.connect()
        self.uid = 'question-owner'
        self.pid = 'question-project'
        self.aid = 'question-agent'
        db.q('INSERT OR REPLACE INTO users (id,name) VALUES (?,?)', (self.uid, 'Owner'))
        db.q('INSERT OR REPLACE INTO projects (id,user_id,name) VALUES (?,?,?)', (self.pid, self.uid, 'Project'))
        db.q('INSERT OR REPLACE INTO agents (id,project_id,nick,lane) VALUES (?,?,?,?)', (self.aid, self.pid, 'Agent', 'code'))
        db.q('DELETE FROM agent_questions WHERE project_id=?', (self.pid,))

    def test_answer_owner_and_late(self):
        qid = questions.create(self.pid, self.aid, 'Какой цвет?', seconds=2)
        self.assertFalse(questions.answer(qid, 'other-user', 'синий'))
        self.assertTrue(questions.answer(qid, self.uid, 'зелёный'))
        self.assertFalse(questions.answer(qid, self.uid, 'повтор'))
        self.assertEqual(questions.wait(qid, threading.Event()), ('answered', 'зелёный'))
        self.assertEqual(questions.list_for('other-user'), [])
        self.assertEqual(questions.list_for(self.uid)[0]['answer'], 'зелёный')

    def test_timeout_and_late_answer(self):
        qid = questions.create(self.pid, self.aid, 'Выбор?', seconds=0.05)
        self.assertEqual(questions.wait(qid, threading.Event(), 0.01)[0], 'timeout')
        self.assertFalse(questions.answer(qid, self.uid, 'поздно'))
        self.assertEqual(questions.get(qid)['status'], 'timeout')

    def test_stop_cancels(self):
        qid = questions.create(self.pid, self.aid, 'Вопрос')
        stop = threading.Event(); stop.set()
        self.assertEqual(questions.wait(qid, stop)[0], 'cancelled')
        self.assertEqual(questions.get(qid)['status'], 'cancelled')

    def test_notification_and_suggestion(self):
        content = questions.notification_content('Ника', 'Магазин', 'Какой выбрать цвет?')
        self.assertIn('Ваш автономный агент задал вопрос', content['spoken'])
        self.assertNotIn('Ника', content['spoken'])
        self.assertIn('Какой выбрать цвет?', content['body'])
        qid = questions.create(self.pid, self.aid, 'Цвет?', suggestion='Синий')
        self.assertEqual(questions.list_for(self.uid)[0]['suggestion'], 'Синий')
        self.assertTrue(questions.answer(qid, self.uid, 'Подтверждаю предложенный вариант: Синий'))
        self.assertIn('Синий', questions.get(qid)['answer'])

    def test_notification_is_passed_as_data(self):
        import base64
        import json
        import types
        payload = "Опасный текст ' ; Write-Output 'НЕ КОМАНДА"
        sent = {}
        class FakeProcess:
            def communicate(self, input):
                sent['input'] = input
        def fake_popen(args, stdin, stdout, stderr, creationflags):
            sent['script'] = base64.b64decode(args[-1]).decode('utf-16le')
            return FakeProcess()
        with patch.object(questions, 'os', types.SimpleNamespace(name='nt')), patch.object(questions.subprocess, 'Popen', side_effect=fake_popen):
            worker = questions.notify_windows('Агент', 'Проект', payload)
            worker.join(timeout=2)
        self.assertNotIn(payload, sent['script'])
        self.assertIn(payload, json.loads(sent['input'].decode('utf-8'))['body'])
        self.assertIn('question-alert.wav', json.loads(sent['input'].decode('utf-8'))['audio'])

    def test_tool_timeout_instructs_agent(self):
        agent = {'id': self.aid, 'project_id': self.pid, 'nick': 'Agent', 'lane': 'code'}
        class Runtime:
            stop = threading.Event()
        real_create = questions.create
        with patch.object(questions, 'create', side_effect=lambda pid, aid, text, suggestion='': real_create(pid, aid, text, seconds=0.03, suggestion=suggestion)), patch.object(questions, 'notify_windows'):
            result = orchestrator.MANAGER._exec_action(self.pid, agent, {}, {'tool':'ask_user','question':'Как?'}, 'Agent', Runtime())
        self.assertIn('Самостоятельно выбери', result)
        self.assertEqual(questions.list_for(self.uid)[0]['status'], 'timeout')


if __name__ == '__main__':
    unittest.main()
