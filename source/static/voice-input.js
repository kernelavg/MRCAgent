(() => {
  const button = document.getElementById('btnVoiceInput');
  const field = document.getElementById('composer');
  const status = document.getElementById('voiceStatus');
  if (!button || !field || !status) return;
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null;
  let active = false;
  let accepted = new Set();
  let discard = false;
  let starting = false;
  function announce(message) {
    status.hidden = !message;
    status.textContent = message;
  }
  function setActive(value) {
    active = value;
    button.classList.toggle('listening', value);
    button.setAttribute('aria-pressed', String(value));
    button.setAttribute('aria-label', value ? 'Остановить голосовой ввод' : 'Начать голосовой ввод');
    button.title = value ? 'Остановить запись' : 'Диктовать запрос';
  }
  if (!Recognition) {
    button.disabled = true;
    button.title = 'Голосовой ввод недоступен в этом браузере. Попробуйте Chrome на localhost.';
    announce('Голосовой ввод недоступен в этом окне. Попробуйте Chrome на localhost.');
    return;
  }
  recognition = new Recognition();
  recognition.lang = 'ru-RU';
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.onstart = () => {
    starting = false;
    setActive(true);
    announce('Слушаю… Речь может обрабатываться сервисом браузера. Нажмите микрофон для остановки.');
  };
  recognition.onresult = (event) => {
    if (discard) return;
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i];
      if (!result.isFinal || accepted.has(i)) continue;
      accepted.add(i);
      const text = result[0] && result[0].transcript ? result[0].transcript.trim() : '';
      if (!text) continue;
      const prefix = field.value && !/\s$/.test(field.value) ? ' ' : '';
      field.value += prefix + text;
      field.dispatchEvent(new Event('input', { bubbles: true }));
    }
  };
  recognition.onerror = (event) => {
    const errors = {
      'not-allowed': 'Нет разрешения на микрофон. Разрешите доступ в браузере и Windows.',
      'service-not-allowed': 'Сервис распознавания отключён или недоступен.',
      'audio-capture': 'Микрофон не найден. Проверьте подключение и настройки Windows.',
      'network': 'Ошибка сети при распознавании речи.',
      'no-speech': 'Речь не обнаружена. Попробуйте ещё раз.',
      'aborted': 'Запись остановлена.'
    };
    starting = false;
    setActive(false);
    announce(errors[event.error] || 'Ошибка распознавания речи: ' + event.error);
  };
  recognition.onend = () => {
    starting = false;
    if (active) announce('Запись завершена. Проверьте текст перед отправкой.');
    setActive(false);
  };
  button.addEventListener('click', () => {
    if (active || starting) {
      recognition.stop();
      return;
    }
    accepted = new Set();
    discard = false;
    try {
      starting = true;
      recognition.lang = window.mrcLanguage && window.mrcLanguage() === 'en' ? 'en-US' : 'ru-RU';
      recognition.start();
    } catch (error) {
      starting = false;
      setActive(false);
      announce('Не удалось запустить микрофон: ' + error.message);
    }
  });
  function stopForSend() {
    if (!active && !starting) return;
    discard = true;
    recognition.abort();
    starting = false;
    setActive(false);
    announce('Запись остановлена перед отправкой.');
  }
  const send = document.getElementById('btnSend');
  if (send) send.addEventListener('click', stopForSend, true);
  field.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) stopForSend();
  }, true);
  // Не отправляем текст автоматически: пользователь может исправить распознавание.
  window.addEventListener('pagehide', () => { if (active) recognition.abort(); });
})();
