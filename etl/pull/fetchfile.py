#!/usr/bin/env python3
"""Download a URL using the Elite browser session (cookies included) and save it.

Runs the fetch inside the logged-in page and returns the body as base64, so no
cookie handling is needed and binary-safe payloads survive.

Usage: python3 fetchfile.py <url> <outfile>
"""
import base64
import json
import os
import sys
import urllib.request

import websockets.sync.client as ws

PORT = 9222

JS = """(async () => {
  const r = await fetch(%s, {credentials: 'include'});
  const buf = new Uint8Array(await r.arrayBuffer());
  let s = '';
  const CH = 0x8000;
  for (let i = 0; i < buf.length; i += CH) s += String.fromCharCode.apply(null, buf.subarray(i, i + CH));
  return JSON.stringify({status: r.status, b64: btoa(s)});
})()"""


def page_ws():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=5) as r:
        pages = [t for t in json.load(r) if t.get("type") == "page"]
    if not pages:
        raise SystemExit("no page target on 9222 - is the Elite window still open?")
    return pages[0]["webSocketDebuggerUrl"]


def main():
    url, out = sys.argv[1], sys.argv[2]
    expr = JS % json.dumps(url)
    with ws.connect(page_ws(), max_size=256 * 1024 * 1024, open_timeout=10) as conn:
        conn.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {"expression": expr, "returnByValue": True,
                       "awaitPromise": True, "userGesture": True},
        }))
        while True:
            data = json.loads(conn.recv(timeout=180))
            if data.get("id") == 1:
                break
    res = data.get("result", {})
    if "exceptionDetails" in res:
        raise SystemExit("JS error: " + json.dumps(res["exceptionDetails"])[:800])
    payload = json.loads(res["result"]["value"])
    body = base64.b64decode(payload["b64"])
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "wb") as f:
        f.write(body)
    print(f"http={payload['status']} bytes={len(body)} -> {out}")


if __name__ == "__main__":
    main()
