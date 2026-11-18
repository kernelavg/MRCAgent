import unittest
import uuid
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:8977'

class LanguageUiTests(unittest.TestCase):
    def test_settings_switch_round_trip_and_user_data(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            ctx = browser.new_context()
            name = 'Lang' + uuid.uuid4().hex[:10]
            result = ctx.request.post(BASE + '/api/auth/register', data={'name':name, 'password':'password-test'}, headers={'Connection':'close'})
            self.assertTrue(result.ok, result.text())
            done = ctx.request.post(BASE + '/api/onboard/done', data={}, headers={'Connection':'close'})
            self.assertTrue(done.ok, done.text())
            page = ctx.new_page()
            page.goto(BASE, wait_until='domcontentloaded')
            page.locator('#appStartup').wait_for(state='hidden', timeout=20000)
            self.assertEqual(page.locator('html').get_attribute('lang'), 'ru')
            self.assertTrue(page.locator('#mrcLanguage').is_hidden())
            page.locator('.rail-btn[data-view=settings]').click()
            selector = page.locator('#mrcLanguage')
            self.assertTrue(selector.is_visible())
            self.assertEqual(page.locator('#languageSettings h4').inner_text(), 'Язык интерфейса')
            selector.select_option('en')
            self.assertEqual(page.locator('html').get_attribute('lang'), 'en')
            self.assertEqual(page.locator('#languageSettings h4').inner_text(), 'Interface language')
            self.assertGreater(page.locator('#panelBody .li-title').filter(has_text='Interface language').count(), 0)
            self.assertEqual(page.locator('.rail-btn[data-view=chats] span').text_content(), 'Chats')
            page.wait_for_function("Array.from(document.querySelectorAll('button')).some(x => x.textContent === 'Agent questions')")
            self.assertEqual(page.locator('#loginNick').get_attribute('placeholder'), 'e.g. alex')
            self.assertIn('Ask Mercury', page.locator('#composer').get_attribute('placeholder'))
            self.assertEqual(page.locator('#chatEmpty h2').inner_text(), 'What shall we create today?')
            page.locator('.rail-btn[data-view=chats]').click()
            self.assertTrue(selector.is_hidden())
            page.evaluate('''() => {
              const box = document.getElementById('messages');
              const item = document.createElement('div'); item.className = 'msg-text';
              item.textContent = 'Настройки'; box.appendChild(item);
              const title = document.createElement('div'); title.className = 'li-title';
              title.id = 'userTitleTest'; title.textContent = 'Настройки';
              document.getElementById('panelBody').appendChild(title);
              const msg = document.createElement('div'); msg.className = 'msg assistant';
              msg.innerHTML = '<div class="msg-body"><div class="msg-text">Пароль</div></div>';
              box.appendChild(msg); window.attachMessageVoice(msg, () => 'Пароль');
            }''')
            self.assertEqual(page.locator('#messages .msg-text').first.text_content(), 'Настройки')
            self.assertEqual(page.locator('#userTitleTest').text_content(), 'Настройки')
            page.wait_for_function("document.querySelector('.msg-speak').textContent === 'Speak'")
            page.reload(wait_until='domcontentloaded')
            self.assertEqual(page.locator('html').get_attribute('lang'), 'en')
            self.assertEqual(page.locator('#mrcLanguage').input_value(), 'en')
            self.assertEqual(page.locator('.login-card h1').text_content(), 'Welcome')
            page.locator('#appStartup').wait_for(state='hidden', timeout=20000)
            page.locator('.rail-btn[data-view=settings]').click()
            selector.select_option('ru')
            self.assertEqual(page.locator('#languageSettings h4').inner_text(), 'Язык интерфейса')
            self.assertEqual(page.locator('.login-card h1').text_content(), 'Добро пожаловать')
            self.assertEqual(page.locator('#loginNick').get_attribute('placeholder'), 'например, alex')
            ctx.close()
            browser.close()

if __name__ == '__main__':
    unittest.main(verbosity=2)
