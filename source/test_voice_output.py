import io
import os
import unittest
import uuid
import wave
from playwright.sync_api import sync_playwright

BASE = os.environ.get('MOBILE_TEST_BASE', 'http://127.0.0.1:8975')

class VoiceOutputTests(unittest.TestCase):
    def test_real_piper_api_and_button(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
            ctx = browser.new_context()
            denied = ctx.request.post(BASE + '/api/voice/speak', data={'text':'Привет'}, headers={'Connection':'close'})
            self.assertEqual(denied.status, 401)
            name = 'Voice' + uuid.uuid4().hex[:10]
            reg = ctx.request.post(BASE + '/api/auth/register', data={'name':name,'password':'password-test'}, headers={'Connection':'close'})
            self.assertTrue(reg.ok, reg.text())
            bad = ctx.request.post(BASE + '/api/voice/speak', data={'text':''}, headers={'Connection':'close'})
            self.assertEqual(bad.status, 400)
            origin = ctx.request.post(BASE + '/api/voice/speak', data={'text':'Привет'}, headers={'Origin':'https://bad.example','Connection':'close'})
            self.assertEqual(origin.status, 403)
            response = ctx.request.post(BASE + '/api/voice/speak', data={'text':'Привет! Это голос Руслан.'}, headers={'Connection':'close'}, timeout=90000)
            self.assertEqual(response.status, 200, response.text() if response.status != 200 else '')
            self.assertEqual(response.headers.get('content-type'), 'audio/wav')
            with wave.open(io.BytesIO(response.body()), 'rb') as sound:
                self.assertEqual(sound.getframerate(), 22050)
                self.assertGreater(sound.getnframes(), 22050)
            page = ctx.new_page()
            page.add_init_script('''window.Audio = class {
                constructor(url){ this.url=url; window.lastAudio=this; }
                play(){ window.audioPlayed=true; return Promise.resolve(); }
                pause(){ window.audioPaused=true; }
            };''')
            page.goto(BASE + '/', wait_until='domcontentloaded')
            page.evaluate('''() => {
                const msg = document.createElement('div'); msg.id = 'voiceTest';
                msg.innerHTML = '<div class="msg-body"><div class="msg-text">Привет!</div></div>';
                document.body.appendChild(msg);
                window.attachMessageVoice(msg, () => 'Привет, как дела?');
            }''')
            page.locator('#voiceTest .msg-speak').dispatch_event('click')
            page.wait_for_function('window.audioPlayed === true', timeout=90000)
            self.assertEqual(page.locator('#voiceTest .msg-speak').get_attribute('aria-pressed'), 'true')
            page.locator('#voiceTest .msg-speak').dispatch_event('click')
            self.assertTrue(page.evaluate('window.audioPaused'))
            self.assertEqual(page.locator('#voiceTest .msg-speak').get_attribute('aria-pressed'), 'false')
            ctx.close()
            browser.close()

if __name__ == '__main__':
    unittest.main(verbosity=2)
