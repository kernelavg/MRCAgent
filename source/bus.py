#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bus.py — крошечная шина событий для SSE: агенты публикуют, браузер слушает."""

import json
import queue
import threading

_subs = set()
_lock = threading.Lock()


class Sub:
    def __init__(self):
        self.q = queue.Queue(maxsize=4000)

    def put(self, item):
        try:
            self.q.put_nowait(item)
        except queue.Full:
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass
            try:
                self.q.put_nowait(item)
            except queue.Full:
                pass


def subscribe():
    s = Sub()
    with _lock:
        _subs.add(s)
    return s


def unsubscribe(s):
    with _lock:
        _subs.discard(s)


def publish(topic, payload):
    """topic: 'project:<id>' | 'chat:<id>' | 'global'"""
    msg = {"topic": topic, "payload": payload}
    with _lock:
        subs = list(_subs)
    for s in subs:
        s.put(msg)


def counts():
    with _lock:
        return len(_subs)


def sse_format(topic, payload):
    data = json.dumps({"topic": topic, "payload": payload}, ensure_ascii=False)
    return "data: %s\n\n" % data
