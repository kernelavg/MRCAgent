"""Сохранённые вопросы агентов и ограниченное ожидание ответа."""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path
import threading
import time
import uuid

import db


def create(pid, agent, text, seconds=120, suggestion=""):
    text = str(text or '').strip()
    if not text:
        raise ValueError('укажите текст вопроса')
    now = time.time()
    qid = uuid.uuid4().hex
    db.q('INSERT INTO agent_questions (id,project_id,agent_id,question,status,created_at,deadline,suggestion) VALUES (?,?,?,?,?,?,?,?)',
         (qid, pid, agent, text, 'pending', now, now + seconds, str(suggestion or '').strip()))
    return qid


def get(qid):
    return db.one('SELECT id,project_id,agent_id,question,status,answer,created_at,deadline,resolved_at FROM agent_questions WHERE id=?', (qid,))


def expire(qid):
    db.q("UPDATE agent_questions SET status='timeout',resolved_at=? WHERE id=? AND status='pending' AND deadline<=?", (time.time(), qid, time.time()))
    return get(qid)


def answer(qid, user_id, text):
    text = str(text or '').strip()
    if not text:
        return False
    now = time.time()
    return db.q("UPDATE agent_questions SET status='answered',answer=?,resolved_at=? WHERE id=? AND status='pending' AND deadline>? AND project_id IN (SELECT id FROM projects WHERE user_id=?)", (text, now, qid, now, user_id)).rowcount == 1


def cancel(qid):
    db.q("UPDATE agent_questions SET status='cancelled',resolved_at=? WHERE id=? AND status='pending'", (time.time(), qid))


def list_for(user_id):
    db.q("UPDATE agent_questions SET status='timeout',resolved_at=? WHERE status='pending' AND deadline<=? AND project_id IN (SELECT id FROM projects WHERE user_id=?)", (time.time(), time.time(), user_id))
    return db.many('SELECT q.id,q.project_id,q.agent_id,q.question,q.status,q.answer,q.created_at,q.deadline,q.resolved_at,q.decision,q.suggestion,p.name AS project_name,a.nick AS agent_name FROM agent_questions q JOIN projects p ON p.id=q.project_id LEFT JOIN agents a ON a.id=q.agent_id WHERE p.user_id=? ORDER BY q.created_at DESC LIMIT 100', (user_id,))


def wait(qid, stop_event, interval=0.5):
    while True:
        if stop_event.is_set():
            cancel(qid)
            return 'cancelled', ''
        row = expire(qid)
        if not row:
            return 'cancelled', ''
        if row['status'] != 'pending':
            return row['status'], row.get('answer') or ''
        stop_event.wait(min(interval, max(0.01, row['deadline'] - time.time())))


def notification_content(agent_name, project_name, question):
    agent_name = str(agent_name or 'Агент').strip()
    project_name = str(project_name or 'проект').strip()
    question = str(question or '').strip()
    title = 'MRC Agent — вопрос от ' + agent_name
    body = 'Проект: ' + project_name + '\nВопрос: ' + question
    spoken = ('Внимание! Ваш автономный агент задал вопрос. Пожалуйста, откройте приложение. '
              'Вы можете подтвердить предложенный вариант или вписать свой ответ.')
    return {'title': title, 'body': body, 'spoken': spoken}


def notify_windows(agent_name, project_name, question):
    """Системное уведомление и озвучка в сеансе Windows, без вставки вопроса в код."""
    if os.name != 'nt':
        return None
    data = notification_content(agent_name, project_name, question)
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    data['audio'] = str(root / 'static' / 'question-alert.wav')
    script = '''[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$inputText = [Console]::In.ReadToEnd()
$data = $inputText | ConvertFrom-Json
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.Visible = $true
$n.ShowBalloonTip(15000, [string]$data.title, [string]$data.body, [System.Windows.Forms.ToolTipIcon]::Info)
try {
    $player = New-Object System.Media.SoundPlayer
    $player.SoundLocation = [string]$data.audio
    $player.Load()
    $player.Play()
} catch {
    try { $s = New-Object -ComObject SAPI.SpVoice; $null = $s.Speak([string]$data.spoken, 16) } catch {}
}
for ($i = 0; $i -lt 150; $i++) { [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 100 }
$n.Dispose()'''
    def run():
        try:
            encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
            proc = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            proc.communicate(input=json.dumps(data, ensure_ascii=False).encode('utf-8'))
        except (OSError, ValueError):
            pass
    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker
