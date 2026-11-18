import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
SCRIPT = (ROOT / 'static' / 'voice-input.js').read_text(encoding='utf-8')
HTML = '<button id="btnVoiceInput"></button><textarea id="composer"></textarea><button id="btnSend"></button><span id="voiceStatus" hidden></span>'

class VoiceInputTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch(headless=True, args=['--no-sandbox'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.p.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.set_content(HTML)

    def tearDown(self):
        self.page.close()

    def setup_fake(self):
        self.page.evaluate('''() => {
          window.SpeechRecognition = class {
            constructor() { window.rec = this; }
            start() { this.onstart(); }
            stop() { this.onend(); }
            abort() { this.onend(); }
            result(text, final, index = 0) {
              const r = [{transcript:text}]; r.isFinal = final;
              const all = []; all[index] = r;
              this.onresult({resultIndex:index,results:all});
            }
          }
        }''')
        self.page.add_script_tag(content=SCRIPT)

    def test_transcript_is_reviewable_and_not_duplicated(self):
        self.setup_fake()
        self.page.fill('#composer', 'Существующий текст')
        self.page.click('#btnVoiceInput')
        self.page.evaluate("rec.result('проверь код', false)")
        self.assertEqual(self.page.input_value('#composer'), 'Существующий текст')
        self.page.evaluate("rec.result('проверь код', true); rec.result('проверь код', true)")
        self.assertEqual(self.page.input_value('#composer'), 'Существующий текст проверь код')
        self.assertEqual(self.page.get_attribute('#btnVoiceInput', 'aria-pressed'), 'true')
        self.page.click('#btnVoiceInput')
        self.assertEqual(self.page.get_attribute('#btnVoiceInput', 'aria-pressed'), 'false')
        self.page.click('#btnVoiceInput')
        self.page.evaluate("rec.result('ещё', true)")
        self.assertEqual(self.page.input_value('#composer'), 'Существующий текст проверь код ещё')

    def test_abort_discards_late_result_and_denial_reported(self):
        self.setup_fake()
        self.page.click('#btnVoiceInput')
        self.page.click('#btnSend')
        self.page.evaluate("rec.result('запоздалый текст', true)")
        self.assertEqual(self.page.input_value('#composer'), '')
        self.page.click('#btnVoiceInput')
        self.page.evaluate("rec.onerror({error:'not-allowed'}); rec.onend()")
        self.assertIn('разрешения', self.page.inner_text('#voiceStatus'))
        self.assertEqual(self.page.get_attribute('#btnVoiceInput', 'aria-pressed'), 'false')

    def test_selected_language_sets_recognition(self):
        self.page.evaluate("window.mrcLanguage = () => 'en'")
        self.setup_fake()
        self.page.click('#btnVoiceInput')
        self.assertEqual(self.page.evaluate('rec.lang'), 'en-US')
        self.page.click('#btnVoiceInput')
        self.page.evaluate("window.mrcLanguage = () => 'ru'")
        self.page.click('#btnVoiceInput')
        self.assertEqual(self.page.evaluate('rec.lang'), 'ru-RU')

    def test_missing_recognition_fails_clearly(self):
        self.page.evaluate('window.SpeechRecognition = undefined; window.webkitSpeechRecognition = undefined')
        self.page.add_script_tag(content=SCRIPT)
        self.assertTrue(self.page.is_disabled('#btnVoiceInput'))
        self.assertIn('недоступен', self.page.inner_text('#voiceStatus'))

if __name__ == '__main__':
    unittest.main(verbosity=2)
