"""Импорт доверенных расширений: markdown и настройки MCP, без запуска кода при импорте."""
import base64
import binascii
import io
import json
import re
import zipfile
from pathlib import PurePosixPath

import db
import mcp


def _parse_json(raw, label):
    try:
        result = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeError, ValueError) as exc:
        raise ValueError('Не удалось прочитать JSON %s: %s' % (label, exc)) from exc
    if not isinstance(result, dict):
        raise ValueError('JSON %s должен быть объектом' % label)
    return result


def inspect_archive(filename, payload):
    try:
        encoded = str(payload or '')
        if encoded.startswith('data:') and ';base64,' in encoded:
            encoded = encoded.split(';base64,', 1)[1]
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError('Неверные данные файла') from exc
    if not raw:
        raise ValueError('Файл пустой')
    name = str(filename or 'plugin').rsplit('/', 1)[-1].rsplit('\\', 1)[-1]
    result = {'name': name, 'description': '', 'docs': [], 'servers': [], 'warnings': []}
    configs = []
    if name.lower().endswith('.zip'):
        try:
            z = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise ValueError('Не удалось прочитать ZIP') from exc
        with z:
            files = {}
            for info in z.infolist():
                if info.is_dir():
                    continue
                path = info.filename.replace('\\', '/')
                parts = PurePosixPath(path).parts
                if path.startswith('/') or '..' in parts or not parts or ':' in parts[0]:
                    raise ValueError('Небезопасный путь в ZIP')
                if info.flag_bits & 1:
                    raise ValueError('ZIP с паролем не поддерживается')
                if path in files:
                    raise ValueError('В ZIP повторяется имя файла')
                files[path] = info
            manifests = [p for p in files if p.endswith('.claude-plugin/plugin.json')]
            if len(manifests) > 1:
                raise ValueError('В архиве несколько плагинов; загрузите каждый отдельно')
            root = manifests[0][:-len('.claude-plugin/plugin.json')] if manifests else ''
            if not root:
                prefixes = {p.split('/')[0] for p in files if '/' in p}
                rootfiles = [p for p in files if '/' not in p]
                if len(prefixes) == 1 and not rootfiles:
                    root = next(iter(prefixes)) + '/'
            if manifests:
                meta = _parse_json(z.read(files[manifests[0]]), 'plugin.json')
                result['name'] = str(meta.get('name') or name)
                result['description'] = str(meta.get('description') or '')
                for key in ('hooks', 'agents', 'lspServers', 'outputStyles'):
                    if meta.get(key):
                        result['warnings'].append('Компонент %s не поддерживается.' % key)
                if isinstance(meta.get('mcpServers'), dict):
                    configs.append({'mcpServers': meta['mcpServers']})
                elif meta.get('mcpServers'):
                    result['warnings'].append('Пути MCP из манифеста не поддерживаются; нужен .mcp.json.')
                for key in ('skills', 'commands'):
                    if meta.get(key):
                        result['warnings'].append('Дополнительные пути %s из манифеста не читаются; используются стандартные папки.' % key)
            unsupported = set()
            for path, info in files.items():
                if root and not path.startswith(root):
                    continue
                rel = path[len(root):]
                low = rel.lower()
                if low.endswith('.md') and ((low.startswith('skills/') and low.endswith('/skill.md')) or low.startswith('commands/')):
                    try:
                        text = z.read(info).decode('utf-8-sig').strip()
                    except UnicodeError as exc:
                        raise ValueError('Markdown должен быть в UTF-8: %s' % rel) from exc
                    if text:
                        result['docs'].append({'name': rel, 'text': text})
                elif low == '.mcp.json':
                    configs.append(_parse_json(z.read(info), rel))
                elif low.startswith(('hooks/', 'agents/')):
                    unsupported.add(rel.split('/')[0])
                elif low not in ('.claude-plugin/plugin.json', 'readme.md', 'license', 'license.md'):
                    unsupported.add('прочие файлы, ресурсы и скрипты')
            for part in sorted(unsupported):
                result['warnings'].append('Не импортируется и не выполняется: %s.' % part)
    elif name.lower().endswith('.json'):
        configs.append(_parse_json(raw, name))
        result['name'] = re.sub(r'\.json$', '', name, flags=re.I)
    elif name.lower().endswith(('.md', '.markdown')):
        try:
            text = raw.decode('utf-8-sig').strip()
        except UnicodeError as exc:
            raise ValueError('Markdown должен быть в UTF-8') from exc
        result['docs'].append({'name': name, 'text': text})
        result['name'] = name.rsplit('.', 1)[0]
    else:
        raise ValueError('Выберите ZIP плагина, MCP JSON или Markdown')
    for config in configs:
        servers = config.get('mcpServers', config)
        if not isinstance(servers, dict):
            raise ValueError('mcpServers должен быть объектом')
        for server_name, spec in servers.items():
            if not isinstance(spec, dict):
                result['warnings'].append('MCP %s: неверная конфигурация.' % server_name)
                continue
            kind = str(spec.get('type') or ('http' if spec.get('url') else 'stdio')).lower()
            serialized = json.dumps(spec)
            if '${' in serialized or '${CLAUDE_PLUGIN_ROOT}' in serialized:
                result['warnings'].append('MCP %s: переменные и пути плагина не подставляются; настройте сервер вручную.' % server_name)
                continue
            if kind not in ('stdio', 'http'):
                result['warnings'].append('MCP %s: транспорт %s не поддерживается.' % (server_name, kind))
                continue
            try:
                rec = mcp.clean_server(name=str(result['name']) + ' / ' + str(server_name),
                                       transport=kind, command=spec.get('command'), args=spec.get('args'),
                                       url=spec.get('url'), headers=spec.get('headers'), env=spec.get('env'), enabled=0)
                result['servers'].append(rec)
            except (ValueError, TypeError) as exc:
                result['warnings'].append('MCP %s: %s' % (server_name, exc))
    if not result['docs'] and not result['servers']:
        raise ValueError('Не найдено поддерживаемых компонентов (skills, commands или MCP). ' + ' '.join(result['warnings']))
    return result


def install(user_id, content):
    pid = db.rid('pl_')
    folder = db.DATA / 'plugins' / pid
    folder.mkdir(parents=True, exist_ok=False)
    for server in content['servers']:
        server['name'] = server['name'] + ' [' + pid[3:] + ']'
    entries = []
    try:
        for i, doc in enumerate(content['docs']):
            filename = str(i) + '.md'
            (folder / filename).write_text(doc['text'], encoding='utf-8')
            entries.append({'name': doc['name'], 'file': filename})
        return db.add_plugin(user_id, pid, content, entries)
    except Exception:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
        raise


def prompt(user_id):
    chunks = []
    for row in db.list_plugins(user_id):
        if not row['enabled']:
            continue
        for doc in db.jload(row['docs'], []):
            try:
                text = (db.DATA / 'plugins' / row['id'] / doc['file']).read_text(encoding='utf-8')
            except (OSError, UnicodeError):
                continue
            chunks.append('\n### %s / %s\n%s' % (row['name'], doc['name'], text))
    return ('ПЛАГИНЫ: инструкции включённых плагинов для агентов\n' + '\n'.join(chunks) + '\n') if chunks else ''
