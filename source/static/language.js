(() => {
  'use strict';
  const translations = {
    'Запуск MRC Agent':'Starting MRCAgent',
    'Подготавливаем рабочее пространство':'Preparing your workspace',
    'О приложении MRC Agent':'About MRCAgent',
    'ВАШЕ РАБОЧЕЕ ПРОСТРАНСТВО':'YOUR WORKSPACE',
    'От идеи':'From idea', 'к результату':'to result',
    'Планируйте, создавайте и проверяйте проекты вместе с командой цифровых агентов.':'Plan, build and test projects with a team of digital agents.',
    'Опишите задачу':'Describe your task','Наблюдайте за работой':'Watch the work','Заберите результат':'Get the result',
    'СОЗДАНО ДЛЯ ВАШИХ ИДЕЙ':'MADE FOR YOUR IDEAS', 'Добро пожаловать':'Welcome',
    'Войдите или создайте аккаунт, чтобы продолжить работу.':'Sign in or create an account to continue.',
    'Вход':'Sign in', 'Регистрация':'Register', 'Никнейм':'Username', 'например, alex':'e.g. alex',
    'Пароль':'Password', 'минимум 6 символов':'at least 6 characters', 'показать пароль':'show password',
    'Пароль ещё раз':'Confirm password','повторите пароль':'repeat password','Войти':'Sign in',
    'Пароль хранится только у вас: PBKDF2-хэш в':'Your password is stored locally as a PBKDF2 hash in',
    ', наружу ничего не уходит.':'; it is not sent anywhere.', 'агентов в рое':'agents in the swarm',
    'всё сохраняется':'everything is saved','работают в фоне':'working in the background',
    'Данные хранятся локально. Запросы к модели отправляются выбранному провайдеру.':'Data is stored locally. Model requests are sent to your chosen provider.',
    'Чаты':'Chats','Агенты':'Agents','Проекты':'Projects','Файлы':'Files','Скиллы':'Skills','Плагины':'Plugins',
    'Музыка: вкл/выкл':'Music: on/off','Играет':'Playing','Ещё':'More','Профиль':'Profile','Создать':'Create',
    'Поиск…':'Search…','Глубина рассуждения для новых запросов':'Reasoning depth for new requests','Уровень Effort':'Effort level',
    'Команды':'Commands','ВАШ AI WORKSPACE':'YOUR AI WORKSPACE','Что создадим сегодня':'What shall we create today',
    'Начните с вопроса или доверьте проект команде цифровых агентов.':'Ask a question or give a project to a team of digital agents.',
    'ПОПРОБУЙТЕ НАЧАТЬ С ЭТОГО':'TRY STARTING WITH THIS','Телеграм-бот':'Telegram bot','Заявки, база и админка':'Requests, database and admin panel',
    'Парсер и дашборд':'Parser and dashboard','Сбор и визуализация данных':'Collect and visualize data','Лендинг':'Landing page',
    'Идея, структура и интерфейс':'Idea, structure and interface','Проверка кода':'Code review','Найти ошибки и улучшения':'Find bugs and improvements',
    'Спросите Mercury…  (Enter — отправить, Shift+Enter — перенос)':'Ask Mercury…  (Enter to send, Shift+Enter for a new line)',
    'Диктовать запрос':'Dictate message','Начать голосовой ввод':'Start voice input','Остановить голосовой ввод':'Stop voice input',
    'Запустить рой агентов':'Start agent swarm','Отправить':'Send','Mercury 3 · ответ приходит потоком':'Mercury 3 · streaming response',
    'нет проекта':'no project','в работе':'in progress','готово':'done','токенов':'tokens','стоимость':'cost',
    'по реальным токенам модели; в демо-режиме — ноль':'based on actual model tokens; zero in demo mode',
    'Пауза':'Pause','Стоп':'Stop','Запустить':'Start','Найти агента…':'Find an agent…','Пока нет проекта с агентами':'No agent project yet',
    'Создайте проект — рой из 100 агентов сразу разберёт его на задачи.':'Create a project and the agent swarm will split it into tasks.',
    'Создать проект':'Create project','Новый проект':'New project','Что должны сделать 100 агентов?':'What should the agents do?',
    'Опишите цель одной фразой — рой сам разложит её на файлы и задачи.':'Describe your goal in one sentence; the swarm will plan files and tasks.',
    'Например: телеграм-бот приёма заявок с sqlite и админкой':'For example: a Telegram request bot with SQLite and an admin panel',
    'Прикрепить файл':'Attach file','любой формат · в запросе сослаться можно через':'any format · reference the file in your request with',
    'Агентов':'Agents','Создать и запустить':'Create and start','Ранняя версия: некоторые функции и платформы ещё не проверены':'Early version: some features and platforms have not been tested',
    'РАННЯЯ ВЕРСИЯ':'EARLY VERSION','Открыть файлы':'Open files','РАБОЧЕЕ ПРОСТРАНСТВО':'WORKSPACE','Сборка проекта':'Build project',
    'Выберите проект и платформу, затем проверьте окружение.':'Select a project and platform, then check the environment.',
    'Настройки сборки':'Build settings','Проект':'Project','Платформа':'Platform','Например, App':'For example, App','Где собирать':'Build location',
    'Локально на этом компьютере':'Locally on this computer','GitHub Actions · подготовка workflow':'GitHub Actions · prepare workflow',
    'Локальная сборка запускает код проекта. Используйте только доверенные проекты.':'A local build runs project code. Use trusted projects only.',
    'Проверить инструменты':'Check tools','Собрать':'Build','Остановить':'Stop','Скачать проект ZIP':'Download project ZIP',
    'Выберите проект и платформу.':'Select a project and platform.','Результат':'Result','Пока сборок нет':'No builds yet',
    'После сборки здесь появятся статус и готовые файлы.':'The status and output files will appear here after the build.',
    'О сборках и ограничениях':'About builds and limitations',
    'Проверяйте готовое приложение на устройстве. iOS Simulator требует macOS и Xcode: результат без подписи, это не IPA для iPhone. GitHub Actions пока запускается вручную.':'Test the finished application on a device. iOS Simulator requires macOS and Xcode; the unsigned output is not an iPhone IPA. GitHub Actions must be started manually.',
    'Вывод сборки':'Build output','Ожидание запуска…':'Waiting to start…','Общий workspace проекта или личный workspace агента':'Project workspace or agent workspace',
    'Код':'Code','Прикрепить файл к проекту':'Attach a file to the project','Сохранить':'Save','Копировать':'Copy','Скачать':'Download','Обновить':'Refresh',
    'выберите файл':'select a file','// файл не выбран':'// no file selected','Скиллы агентов':'Agent skills','свои .md-инструкции, которые видит каждый агент':'custom .md instructions visible to every agent',
    'Импорт .md':'Import .md','Новый скилл':'New skill','Как это работает.':'How it works.','Скиллов пока нет':'No skills yet',
    'Создайте первый — например «Стиль кода» или «Как проверять работу». Или импортируйте готовый .md.':'Create your first skill, such as Code style, or import an existing .md file.',
    'Создать скилл':'Create skill','Вставить примеры':'Insert examples','Плагины агентов':'Agent plugins','Плагины и инструкции для вашего роя':'Plugins and instructions for your swarm',
    'Добавить плагин':'Add plugin','Поддерживаются:':'Supported:','Безопасность:':'Security:','Плагинов пока нет':'No plugins yet',
    'Загрузите ZIP плагина или файл Markdown.':'Upload a plugin ZIP or Markdown file.','Серверы и внешние инструменты агентов':'Agent servers and external tools',
    'Сохранить настройки MCP':'Save MCP settings','MCP — внешние инструменты':'MCP — external tools','MCP включён':'MCP enabled',
    'Показывать инструменты в промпте':'Include tools in prompt','Общий таймаут вызова, сек':'Default call timeout, seconds',
    'Подключённые серверы':'Connected servers','Добавить сервер':'Add server','Подключайте только доверенные серверы: они могут выполнять программы и обращаться к сети.':'Only connect trusted servers: they can run programs and access the network.',
    'Импорт MCP JSON':'Import MCP JSON','Демо-сервер':'Demo server','Файлы на диске':'Files on disk','Сервер по адресу':'Remote server',
    'Имя':'Name','— так агент называет сервер':'— the name agents use for this server','например: файлы проекта':'e.g. project files','Транспорт':'Transport',
    'stdio — команда запуска':'stdio — launch command','http — сервер по адресу':'http — server URL','Таймаут этого сервера, сек (0 — как общий)':'Server timeout, seconds (0 uses default)',
    'Команда запуска':'Launch command','Переменные окружения':'Environment variables','KEY=значение, по одной в строке':'KEY=value, one per line','Адрес сервера':'Server address','Заголовки':'Headers',
    'Name=значение, по одному в строке; Authorization скрывается':'Name=value, one per line; Authorization is hidden','Подключить':'Connect','Очистить форму':'Clear form',
    'Настройки':'Settings','локальный':'local','новый ник':'new username','Сменить':'Change','Выйти':'Sign out','Текущий':'Current','Новый':'New','Сменить пароль':'Change password',
    'Модель':'Model','API-ключ':'API key','API-ключ провайдера':'Provider API key','Очистить':'Clear','Ключ хранится локально в базе приложения.':'The key is stored locally in the app database.',
    'Effort — глубина рассуждения':'Effort — reasoning depth','Параллельных запросов':'Concurrent requests','Проверить провайдера':'Test provider','Демо-режим':'Demo mode',
    'Агенты имитируют работу без API-ключа — удобно посмотреть интерфейс':'Agents simulate work without an API key, so you can explore the interface.',
    'Доступы агентов':'Agent permissions','Терминал':'Terminal','Агенты запускают любые команды: пайпы, cd куда угодно, любые утилиты':'Agents can run arbitrary commands, pipelines and utilities.',
    'Строгий режим команд':'Strict command mode','Вернуть allowlist (python, git, ls…) и запрет разрушительных команд':'Restore the allowlist (python, git, ls…) and block destructive commands.',
    'Интернет':'Internet','Поиск и открытие страниц для разведки':'Search and open web pages for research','Таймаут команды, сек':'Command timeout, seconds',
    'Рой и защита от зацикливания':'Swarm and loop protection','Защита от зацикливания':'Loop protection','Промежуточное слияние, сек':'Intermediate merge interval, seconds',
    'Свой SearXNG (необязательно)':'Custom SearXNG (optional)','Если задан — поиск идёт через ваш SearXNG, а не через DuckDuckGo и Bing.':'When set, searches use your SearXNG rather than DuckDuckGo and Bing.',
    'Почта — агенты читают и отвечают':'Email — agents read and reply','IMAP-сервер':'IMAP server','Порт IMAP':'IMAP port','Шифрование IMAP':'IMAP encryption',
    'без шифрования (локальный сервер)':'no encryption (local server)','Логин (полный адрес)':'Username (full address)','SMTP-сервер':'SMTP server','Порт SMTP':'SMTP port',
    'Шифрование SMTP':'SMTP encryption','Папка по умолчанию':'Default folder','Пароль приложения':'App password','Адрес отправителя':'Sender address',
    'Агенты могут отправлять письма':'Agents may send emails','Выключите — агенты смогут только читать и искать':'Disable to allow agents only to read and search',
    'Проверить связь':'Test connection','Музыка':'Music','Музыка во время работы':'Music while working','Громкость:':'Volume:',
    'Плавно поднимать громкость':'Fade music in','Тема входит тихо и нарастает за пару секунд':'The music starts quietly and fades in over a few seconds',
    'Своя музыка':'Custom music','Загрузить свой трек':'Upload a track','Вернуть встроенную':'Restore built-in track','Обучение':'Tutorial','Первый вход':'First sign-in',
    'Показать обучение снова':'Show tutorial again','С голосом':'With voice','Внешний вид':'Appearance','Акцент':'Accent','Светлая тема':'Light theme','Для светлых экранов и печати':'For light screens and printing',
    'Данные':'Data','База':'Database','Удалить все проекты и чаты':'Delete all projects and chats','Куда перейти или что сделать…':'Where to go or what to do…',
    'выбор':'select','открыть':'open','закрыть':'close','Собираю проект':'Building project','поднимаю рой…':'starting swarm…',
    'Закрыть':'Close','Голос':'Voice','Назад':'Back','Дальше':'Next','Пропустить обучение':'Skip tutorial',
    'Озвучить':'Speak','Остановить запись':'Stop recording','Озвучить голосом Руслан локально':'Speak locally with Ruslan',
    'Язык интерфейса':'Interface language','Язык':'Language',
    'Изменения применяются сразу и сохраняются в этом браузере. Перевод интерфейса пока неполный.':'Changes apply immediately and are saved in this browser. Interface translation is not complete yet.',
    'Вопросы агентов':'Agent questions', 'Пока вопросов нет.':'No questions yet.',
    'После двух минут без ответа агент продолжит самостоятельно.':'After two minutes without an answer, the agent continues on its own.',
    'Новый чат':'New chat', 'Пока нет чатов':'No chats yet', 'Нет проектов':'No projects', 'Пока нет проектов':'No projects yet',
    'Фильтр по роли':'Filter by role', 'Аккаунт':'Account', 'Завершено':'Completed',
    'Серверы и настройки MCP — в основной области':'MCP servers and settings are in the main area',
    'Управление плагинами — в основной области':'Manage plugins in the main area',
    'Выберите проект и запустите сборку в MRCIDE Studio.':'Select a project and start a build in MRCIDE Studio.',
    'Плагины и инструкции для агентов':'Plugins and agent instructions',
    'Скиллы видит каждый агент роя':'Every swarm agent can see skills',
    'Ключ не задан — работает демо-режим.':'No key is set; demo mode is active.',
    'Настройки сохранены':'Settings saved', 'Сообщение скопировано':'Message copied',
    'Копировать сообщение':'Copy message',
    'Голосовой ввод недоступен в этом браузере. Попробуйте Chrome на localhost.':'Voice input is unavailable in this browser. Try Chrome on localhost.',
    'Голосовой ввод недоступен в этом окне. Попробуйте Chrome на localhost.':'Voice input is unavailable in this window. Try Chrome on localhost.',
    'Слушаю… Речь может обрабатываться сервисом браузера. Нажмите микрофон для остановки.':'Listening… Speech may be processed by the browser service. Press the microphone to stop.',
    'Запись завершена. Проверьте текст перед отправкой.':'Recording finished. Review the text before sending.',
    'Запись остановлена перед отправкой.':'Recording stopped before sending.',
    'Нет разрешения на микрофон. Разрешите доступ в браузере и Windows.':'Microphone permission denied. Allow access in your browser and Windows.',
    'Сервис распознавания отключён или недоступен.':'The recognition service is disabled or unavailable.',
    'Микрофон не найден. Проверьте подключение и настройки Windows.':'Microphone not found. Check the connection and Windows settings.',
    'Ошибка сети при распознавании речи.':'Network error during speech recognition.',
    'Речь не обнаружена. Попробуйте ещё раз.':'No speech detected. Please try again.',
    'Запись остановлена.':'Recording stopped.'
  };
  const textRecords = new WeakMap();
  const attributeRecords = new WeakMap();
  let language = 'ru';
  try { if (localStorage.getItem('mrc-ui-language') === 'en') language = 'en'; } catch (error) { }
  function protectedNode(node) {
    const parent = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    if (!parent) return true;
    if (parent.closest('pre, code, [contenteditable="true"], script, style, svg, .msg-text, .msg-role, #chatTitle, #profileName, #profileEmail, .li-title, .file-name')) return true;
    if (node.nodeType === Node.TEXT_NODE && parent.closest('textarea')) return true;
    if (parent.closest('#messages') && !parent.closest('.msg-speak, .msg-copy')) return true;
    return false;
  }
  function translation(original) {
    const key = original.trim();
    if (!Object.prototype.hasOwnProperty.call(translations, key)) return null;
    return original.replace(key, translations[key]);
  }
  function translateText(node) {
    if (protectedNode(node)) return;
    const current = node.nodeValue;
    let original = textRecords.get(node);
    if (original && current !== original && current !== translation(original)) original = null;
    if (!original) {
      if (translation(current) === null) { textRecords.delete(node); return; }
      original = current;
      textRecords.set(node, original);
    }
    const target = language === 'en' ? translation(original) : original;
    if (current !== target) node.nodeValue = target;
  }
  function translateAttrs(el) {
    if (protectedNode(el)) return;
    let known = attributeRecords.get(el);
    if (!known) { known = {}; attributeRecords.set(el, known); }
    for (const name of ['placeholder','title','aria-label']) {
      const current = el.getAttribute(name);
      if (!current) continue;
      let original = known[name];
      if (original && current !== original && current !== translation(original)) original = null;
      if (!original) {
        if (translation(current) === null) { delete known[name]; continue; }
        original = current;
        known[name] = original;
      }
      const target = language === 'en' ? translation(original) : original;
      if (current !== target) el.setAttribute(name, target);
    }
  }
  function apply(root) {
    if (!root) return;
    if (root.nodeType === Node.TEXT_NODE) { translateText(root); return; }
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
    if (root.nodeType === Node.ELEMENT_NODE) translateAttrs(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (node.nodeType === Node.TEXT_NODE) translateText(node);
      else translateAttrs(node);
    }
  }
  function setLanguage(next) {
    language = next === 'en' ? 'en' : 'ru';
    document.documentElement.lang = language;
    try { localStorage.setItem('mrc-ui-language', language); } catch (error) { }
    const selector = document.getElementById('mrcLanguage');
    if (selector) selector.value = language;
    apply(document.body);
    window.dispatchEvent(new CustomEvent('mrc-language-changed', { detail: language }));
  }
  window.mrcLanguage = () => language;
  const selector = document.getElementById('mrcLanguage');
  if (selector) selector.addEventListener('change', () => setLanguage(selector.value));
  setLanguage(language);
  const observer = new MutationObserver((changes) => {
    for (const change of changes) {
      if (change.type === 'characterData') apply(change.target);
      else if (change.type === 'attributes') translateAttrs(change.target);
      else for (const node of change.addedNodes) apply(node);
    }
  });
  observer.observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['title','aria-label','placeholder'] });
})();
