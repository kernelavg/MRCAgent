(() => {
  let audio = null;
  let request = null;
  let owner = null;
  let objectUrl = null;
  let generation = 0;
  function stop() {
    generation += 1;
    if (request) request.abort();
    request = null;
    if (audio) { audio.pause(); audio = null; }
    if (objectUrl) { URL.revokeObjectURL(objectUrl); objectUrl = null; }
    if (owner) { owner.textContent = 'Озвучить'; owner.setAttribute('aria-pressed', 'false'); owner = null; }
  }
  window.attachMessageVoice = (node, getText) => {
    const body = node.querySelector('.msg-body');
    if (!body || body.querySelector('.msg-speak')) return;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn-ghost sm msg-speak';
    button.textContent = 'Озвучить';
    button.title = 'Озвучить голосом Руслан локально';
    button.setAttribute('aria-pressed', 'false');
    body.appendChild(button);
    button.addEventListener('click', async () => {
      if (owner === button) { stop(); return; }
      stop();
      owner = button;
      const current = generation;
      request = new AbortController();
      button.setAttribute('aria-pressed', 'true');
      button.textContent = 'Остановить';
      try {
        const response = await fetch('/api/voice/speak', {
          method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: getText() }), signal: request.signal
        });
        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          throw new Error(data.error || 'Ошибка озвучивания');
        }
        const blob = await response.blob();
        if (generation !== current) return;
        request = null;
        objectUrl = URL.createObjectURL(blob);
        audio = new Audio(objectUrl);
        audio.onended = stop;
        audio.onerror = () => { stop(); if (window.toastVoiceError) window.toastVoiceError('Не удалось воспроизвести звук'); };
        await audio.play();
      } catch (error) {
        if (generation !== current || error.name === 'AbortError') return;
        stop();
        if (window.toastVoiceError) window.toastVoiceError(error.message);
      }
    });
  };
  window.addEventListener('pagehide', stop);
})();
