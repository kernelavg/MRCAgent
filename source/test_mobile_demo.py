from pathlib import Path
import unittest
from playwright.sync_api import sync_playwright

ASSETS = Path(__file__).parent / 'mobile-android' / 'app' / 'src' / 'main' / 'assets' / 'static'
ORIGIN = 'https://demo.mrcagent.invalid'


class OfflineDemoTests(unittest.TestCase):
    def test_demo_interaction_without_network(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            ctx = browser.new_context(viewport={'width':390, 'height':844}, is_mobile=True, has_touch=True)
            ctx.set_offline(True)
            requests = []
            errors = []
            allowed = {'mobile.html':'text/html','mobile.js':'application/javascript','mobile.css':'text/css','demo.js':'application/javascript'}
            def respond(route):
                url = route.request.url
                requests.append(url)
                name = url.split('/')[-1]
                if url.startswith(ORIGIN + '/static/') and name in allowed:
                    route.fulfill(status=200, content_type=allowed[name], body=(ASSETS / name).read_bytes())
                else:
                    route.abort()
            ctx.route('**/*', respond)
            page = ctx.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(ORIGIN + '/static/mobile.html')
            page.locator('#dashboard').wait_for(state='visible')
            self.assertTrue(page.locator('.demo-banner').is_visible())
            self.assertEqual(page.locator('#totalProjects').text_content(),'2')
            page.locator('[data-tab=agents]').click()
            self.assertEqual(page.locator('.agent').count(),5)
            self.assertEqual(page.locator('.agent .avatar svg').count(),5)
            page.locator('[data-tab=build]').click()
            self.assertIn('Настоящий APK не собирался',page.locator('#buildLog').text_content())
            page.locator('[data-tab=questions]').click()
            self.assertEqual(page.locator('.question').count(),1)
            page.locator('.question textarea').fill('Мой тестовый ответ')
            page.get_by_role('button',name='Отправить ответ').click()
            page.get_by_text('Сейчас вопросов нет.',exact=False).wait_for(state='visible')
            page.locator('[data-tab=overview]').click()
            page.locator('#pause').click()
            page.wait_for_function("document.getElementById('projectStatus').textContent === 'Пауза'")
            page.locator('#resume').click()
            page.wait_for_function("document.getElementById('projectStatus').textContent === 'Работает'")
            page.get_by_role('button',name='Демо · Интернет-магазин').click()
            page.wait_for_function("document.getElementById('projectName').textContent.includes('Интернет-магазин')")
            page.locator('#start').click()
            page.wait_for_function("document.getElementById('projectStatus').textContent === 'Работает'")
            page.screenshot(path=str(Path(__file__).parent / 'docs/mobile-demo.png'),full_page=True)
            self.assertFalse(errors,errors)
            self.assertTrue(all(x.startswith(ORIGIN + '/static/') for x in requests),requests)
            self.assertEqual(len(requests),4,requests)
            ctx.close()
            browser.close()


if __name__ == '__main__':
    unittest.main()
