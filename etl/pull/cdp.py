#!/usr/bin/env python3
"""Minimal CDP eval helper for the Elite browser window (port 9222).

Usage:
  python3 cdp.py targets
  python3 cdp.py eval '<js expression>'
  python3 cdp.py evalfile /path/to/snippet.js

The JS runs in the page's main world and may be an async IIFE; the resolved
value is JSON-encoded and printed.
"""
import json
import sys
import urllib.request

import websockets.sync.client as ws

PORT = 9222


def targets():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=5) as r:
        return [t for t in json.load(r) if t.get("type") == "page"]


def page_ws(match=None):
    pages = targets()
    if match:
        pages = [p for p in pages if match.lower() in (p["url"] + p["title"]).lower()]
    if not pages:
        raise SystemExit("no matching page target")
    return pages[0]["webSocketDebuggerUrl"]


def evaluate(js, match=None, timeout=30):
    url = page_ws(match)
    expr = js
    with ws.connect(url, max_size=64 * 1024 * 1024, open_timeout=10) as conn:
        msg = {
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expr,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": True,
            },
        }
        conn.send(json.dumps(msg))
        while True:
            raw = conn.recv(timeout=timeout)
            data = json.loads(raw)
            if data.get("id") == 1:
                break
    res = data.get("result", {})
    if "exceptionDetails" in res:
        raise SystemExit("JS error: " + json.dumps(res["exceptionDetails"])[:1500])
    return res.get("result", {}).get("value")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "targets":
        for t in targets():
            print(f"{t['title'][:60]} | {t['url'][:100]}")
    elif cmd == "call":
        # Raw CDP method call: python3 cdp.py call 'Network.getCookies' '{"urls":["..."]}'
        method = sys.argv[2]
        params = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
        url = page_ws()
        with ws.connect(url, max_size=64 * 1024 * 1024, open_timeout=10) as conn:
            conn.send(json.dumps({"id": 1, "method": method, "params": params}))
            while True:
                data = json.loads(conn.recv(timeout=30))
                if data.get("id") == 1:
                    break
        print(json.dumps(data.get("result"), indent=1))
    elif cmd == "eval":
        print(json.dumps(evaluate(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None), indent=1))
    elif cmd == "evalfile":
        with open(sys.argv[2]) as f:
            js = f.read()
        print(json.dumps(evaluate(js, sys.argv[3] if len(sys.argv) > 3 else None), indent=1))
    else:
        raise SystemExit(__doc__)
