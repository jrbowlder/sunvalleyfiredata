#!/usr/bin/env python3
"""Capture the First Due app's real request headers for /report/ calls.

Enables CDP Network, triggers an in-app refetch (click Reset), collects the
requestWillBeSent events, and prints header NAMES plus redacted values for
anything that looks like a credential.

Usage: python3 net_capture.py [seconds]
"""
import json
import sys
import time
import urllib.request

import websockets.sync.client as ws

PORT = 9222
CLICK_JS = """(() => {
  const clean = s => (s||'').replace(/\\s+/g,' ').trim();
  const b = [...document.querySelectorAll('button,a')].find(x => /^reset$/i.test(clean(x.textContent)));
  if (b) { b.click(); return 'clicked reset'; }
  const p = [...document.querySelectorAll('button,a')].find(x => /^apply$/i.test(clean(x.textContent)));
  if (p) { p.click(); return 'clicked apply'; }
  return 'no trigger button';
})()"""

SECRETISH = ('auth', 'token', 'cookie', 'secret', 'key', 'session')


def target_ws():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=6) as r:
        pages = [t for t in json.load(r) if t.get("type") == "page"]
    fd = [p for p in pages if "firstdue" in p["url"].lower() or "first due" in p.get("title", "").lower()]
    if not fd:
        raise SystemExit("no First Due tab found")
    return fd[0]["webSocketDebuggerUrl"]


def redact(name, value):
    if any(s in name.lower() for s in SECRETISH):
        v = str(value)
        return f"<redacted len={len(v)} prefix={v[:6]}>"
    return value


def main():
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    with ws.connect(target_ws(), max_size=64 * 1024 * 1024, open_timeout=10) as conn:
        mid = 0
        def send(method, params=None):
            nonlocal mid
            mid += 1
            conn.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            return mid

        send("Network.enable")
        mid_click = send("Runtime.evaluate", {"expression": CLICK_JS, "returnByValue": True, "userGesture": True})

        triggered = None
        captured = []
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                raw = conn.recv(timeout=max(1, deadline - time.time()))
            except Exception:
                break
            msg = json.loads(raw)
            if msg.get("id") == mid_click:
                triggered = msg.get("result", {}).get("result", {}).get("value")
            elif msg.get("method") == "Network.requestWillBeSent":
                p = msg["params"]["request"]
                if "/report/" in p.get("url", ""):
                    captured.append({
                        "method": p.get("method"),
                        "url": p["url"].split("?")[0].replace("https://sizeup.firstduesizeup.com", ""),
                        "headers": {k: redact(k, v) for k, v in p.get("headers", {}).items()},
                        "postData": (p.get("postData") or "")[:200]
                    })
        print("trigger:", triggered)
        print("captured:", len(captured))
        for c in captured[:3]:
            print(json.dumps(c, indent=1))
        print("\nstorage keys:")
        send("Runtime.evaluate", {"expression": "JSON.stringify({local:Object.keys(localStorage),session:Object.keys(sessionStorage)})", "returnByValue": True})
        while True:
            m = json.loads(conn.recv(timeout=15))
            if m.get("id") == mid + 0 or m.get("result", {}).get("result", {}).get("value"):
                print(m.get("result", {}).get("result", {}).get("value"))
                break


if __name__ == "__main__":
    main()
