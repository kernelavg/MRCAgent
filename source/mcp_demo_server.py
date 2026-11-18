#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcp_demo_server.py — маленький MCP-сервер на stdlib, чтобы MCP можно было
попробовать сразу, ничего не устанавливая.

Запуск (это и есть команда для Настройки → MCP → команда):
    python mcp_demo_server.py

Инструменты:
  * now       — текущее время и дата (часовой пояс машины);
  * calc      — посчитать арифметическое выражение (+ - / // % ** и скобки);
  * wordcount — сколько слов/строк/символов в тексте;
  * echo      — вернуть текст как есть (проверка связи).

Протокол: JSON-RPC 2.0 построчно в stdin/stdout — транспорт stdio из спецификации MCP.
"""

import ast
import json
import operator
import sys
import time
from datetime import datetime

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "mrc-demo", "version": "1.0.0"}

TOOLS = [
    {
        "name": "now",
        "description": "Текущая дата и время на этой машине",
        "inputSchema": {"type": "object", "properties": {
            "format": {"type": "string", "description": "strftime-формат, по умолчанию %Y-%m-%d %H:%M:%S"}},
            "required": []},
    },
    {
        "name": "calc",
        "description": "Посчитать арифметическое выражение: числа, + - / // % и скобки",
        "inputSchema": {"type": "object", "properties": {
            "expr": {"type": "string", "description": "например (2+3) * 7 / 5"}},
            "required": ["expr"]},
    },
    {
        "name": "wordcount",
        "description": "Статистика текста: слова, строки, символы",
        "inputSchema": {"type": "object", "properties": {
            "text": {"type": "string"}}, "required": ["text"]},
    },
    {
        "name": "echo",
        "description": "Вернуть текст обратно — быстрая проверка, что связь есть",
        "inputSchema": {"type": "object", "properties": {
            "text": {"type": "string"}}, "required": []},
    },
]

def calc(expr):
    """Вычисляет только арифметические выражения, без вызовов и доступа к объектам."""
    binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
              ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
              ast.Mod: operator.mod, ast.Pow: operator.pow}
    unary = {ast.UAdd: operator.pos, ast.USub: operator.neg}
    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            return binary[type(node.op)](visit(node.left), visit(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in unary:
            return unary[type(node.op)](visit(node.operand))
        raise ValueError("разрешена только арифметика")
    try:
        return visit(ast.parse(str(expr or ""), mode="eval").body)
    except (SyntaxError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError(str(exc)) from exc


def call_tool(name, args):
    args = args or {}
    if name == "now":
        fmt = str(args.get("format") or "%Y-%m-%d %H:%M:%S")
        try:
            text = datetime.now().strftime(fmt)
        except ValueError:
            text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return [{"type": "text", "text": "%s (unix %d)" % (text, int(time.time()))}]
    if name == "calc":
        value = calc(args.get("expr") or "")
        return [{"type": "text", "text": str(value)}]
    if name == "wordcount":
        text = str(args.get("text") or "")
        return [{"type": "text", "text": "слов: %d · строк: %d · символов: %d"
                                        % (len(text.split()), len(text.splitlines()), len(text))}]
    if name == "echo":
        return [{"type": "text", "text": str(args.get("text") or "")}]
    return None


def respond(msg_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result},
                                ensure_ascii=False) + "\n")
    sys.stdout.flush()


def respond_error(msg_id, code, message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id,
                                 "error": {"code": code, "message": message}},
                                ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            respond_error(None, -32700, "не JSON")
            continue
        method = msg.get("method") or ""
        mid = msg.get("id")
        params = msg.get("params") or {}
        notify = "id" not in msg
        if method == "initialize":
            respond(mid, {"protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": SERVER_INFO,
                          "instructions": "демо-сервер MRC Agent: now, calc, wordcount, echo"})
        elif method in ("notifications/initialized", "initialized"):
            continue
        elif method == "ping":
            respond(mid, {})
        elif method == "tools/list":
            respond(mid, {"tools": TOOLS})
        elif method == "tools/call":
            name = params.get("name") or ""
            try:
                result = call_tool(name, params.get("arguments") or {})
            except Exception as e:                      # noqa: BLE001
                # ошибка инструмента — это ответ isError, а не смерть процесса:
                # иначе один кривой аргумент от модели роняет сервер целиком
                respond(mid, {"content": [{"type": "text", "text": "%s: %s"
                                                             % (type(e).__name__, e)}],
                              "isError": True})
                continue
            if result is None:
                respond_error(mid, -32602, "нет такого инструмента: %s" % name)
            else:
                respond(mid, {"content": result, "isError": False})
        elif method == "resources/list":
            respond(mid, {"resources": []})
        elif method == "prompts/list":
            respond(mid, {"prompts": []})
        elif notify:
            continue
        else:
            respond_error(mid, -32601, "метод не поддерживается: %s" % method)
    return 0


if __name__ == "__main__":
    sys.exit(main())
