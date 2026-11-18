import os
from pathlib import Path
import unittest
import uuid
from playwright.sync_api import sync_playwright

import db
import questions

BASE = os.environ.get('MOBILE_TEST_BASE', 'http://127.0.0.1:8975')


class MobileBrowserTests(unittest.TestCase):
    def test_mobile_login_project_questions_and_controls(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            ctx = browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
            page = ctx.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            username = 'Mobile' + uuid.uuid4().hex[:12]
            reg = ctx.request.post(BASE + '/api/auth/register', data={'name': username, 'password': 'password-test'}, headers={'Connection': 'close'})
            self.assertTrue(reg.ok, reg.text())
            created = ctx.request.post(BASE + '/api/projects', data={'name': '<b>Проект</b>', 'goal': 'Тест мобильного управления', 'agents': 3}, headers={'Connection':'close'})
            self.assertTrue(created.ok, created.text())
            pid = created.json()['project']['id']
            agent = db.list_agents(pid)[0]
            qid = questions.create(pid, agent['id'], 'Выбрать тему?', seconds=120, suggestion='Тёмная')
            page.goto(BASE + '/static/mobile.html', wait_until='networkidle')
            page.locator('#dashboard').wait_for(state='visible')
            page.get_by_role('button', name='<b>Проект</b>').click()
            page.locator('#projectName').wait_for(state='visible')
            self.assertEqual(page.locator('#projectName').text_content(), '<b>Проект</b>')
            self.assertEqual(page.locator('#projectName b').count(), 0)
            self.assertEqual(page.locator('.agent').count(), 3)
            self.assertEqual(page.locator('.agent .avatar svg').count(), 3)
            page.locator('[data-tab=agents]').click()
            page.locator('.agent').first.wait_for(state='visible')
            page.locator('[data-tab=build]').click()
            page.get_by_text('Нет сборок').wait_for(state='visible')
            page.locator('[data-tab=questions]').click()
            page.get_by_text('Выбрать тему?').wait_for(state='visible')
            page.locator('.question textarea').fill('Свой ответ')
            page.get_by_role('button', name='Отправить ответ').click()
            page.get_by_text('Сейчас вопросов нет.', exact=False).wait_for(state='visible')
            self.assertEqual(questions.get(qid)['answer'], 'Свой ответ')
            qid2 = questions.create(pid, agent['id'], 'Предложенный вариант?', seconds=120, suggestion='Да, подходит')
            page.click('#refresh')
            page.get_by_text('Предложенный вариант?').wait_for(state='visible')
            page.get_by_role('button', name='Подтвердить вариант').click()
            page.get_by_text('Сейчас вопросов нет.', exact=False).wait_for(state='visible')
            self.assertEqual(questions.get(qid2)['answer'], 'Да, подходит')
            stranger = browser.new_context()
            other = stranger.request.post(BASE + '/api/auth/register', data={'name':'Other' + uuid.uuid4().hex[:10], 'password':'password-test'}, headers={'Connection':'close'})
            self.assertTrue(other.ok)
            self.assertEqual(stranger.request.get(BASE + '/api/projects/' + pid).status, 404)
            self.assertEqual(stranger.request.get(BASE + '/api/ide/' + pid + '/builds').status, 404)
            self.assertNotIn(qid, [q['id'] for q in stranger.request.get(BASE + '/api/questions').json()['questions']])
            stranger.close()
            page.locator('[data-tab=overview]').click()
            page.screenshot(path=str(Path(__file__).parent / 'docs/mobile-dashboard-v2.png'), full_page=True)
            page.locator('#start').click()
            page.wait_for_function("document.getElementById('projectStatus').textContent !== 'Ожидает'")
            page.on('dialog', lambda dialog: dialog.accept())
            if page.locator('#stop').is_enabled(): page.locator('#stop').click()
            self.assertFalse(errors, errors)
            page.click('#logout')
            page.locator('#login').wait_for(state='visible')
            page.fill('#name',username);page.fill('#password','password-test')
            page.click('#loginForm button[type=submit]')
            self.assertIn('Подтвердите риск HTTP',page.locator('#loginError').text_content())
            page.check('#acceptHttp');page.click('#loginForm button[type=submit]')
            page.locator('#dashboard').wait_for(state='visible')
            ctx.close();browser.close()


if __name__ == '__main__':
    unittest.main()
