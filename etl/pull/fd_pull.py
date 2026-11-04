#!/usr/bin/env python3
"""Pull a First Due report via its JSON API, using the logged-in browser session.

Runs fetch() inside the First Due page, paging through getResultList, and writes
the combined rows to a JSON file. Read-only: it never creates or modifies a report.

Usage:
  python3 fd_pull.py <reportID> <outfile.json> [filter_key=value ...]
"""
import base64
import json
import os
import sys
import urllib.request

import websockets.sync.client as ws

PORT = 9222
PAGE_SIZE = 1000


def target_ws():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=6) as r:
        pages = [t for t in json.load(r) if t.get("type") == "page"]
    fd = [p for p in pages if "firstdue" in p["url"] or "First Due" in p.get("title", "")]
    if not fd:
        raise SystemExit("no First Due tab found on 9222")
    return fd[0]["webSocketDebuggerUrl"]


JS_TMPL = """(async () => {
  const out = { rows: [], pages: 0, total: null, error: null };
  const base = {filters: %s, module_code: "", executionMode: "replace",
                killScope: "instance", reportClientInstanceId: "capstone_pull", id: %s};
  let page = 0, guard = 0;
  while (guard++ < 60) {
    const body = Object.assign({}, base, {page: page, page_size: %d});
    const r = await fetch('/report/getResultList', {
      method: 'POST', credentials: 'include',
      headers: {'Content-Type': 'application/json;charset=UTF-8',
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json'},
      body: JSON.stringify(body)});
    if (!r.ok) { out.error = 'HTTP ' + r.status; break; }
    const j = await r.json();
    if (j.errorMsg) { out.error = j.errorMsg; }
    const rows = j.items || [];
    if (out.total === null) out.total = (j.itemsTotal && j.itemsTotal[0]) || null;
    if (!rows.length) break;
    out.rows = out.rows.concat(rows);
    out.pages++;
    if (out.total !== null && out.rows.length >= out.total) break;
    if (rows.length < %d) break;
    page++;
  }
  return JSON.stringify(out);
})()"""


def run(js):
    with ws.connect(target_ws(), max_size=256 * 1024 * 1024, open_timeout=10) as conn:
        conn.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": js, "returnByValue": True,
                       "awaitPromise": True, "userGesture": True},
        }))
        while True:
            data = json.loads(conn.recv(timeout=300))
            if data.get("id") == 1:
                break
    res = data.get("result", {})
    if "exceptionDetails" in res:
        raise SystemExit("JS error: " + json.dumps(res["exceptionDetails"])[:900])
    return json.loads(res["result"]["value"])


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    report_id, out = sys.argv[1], sys.argv[2]
    filters = {}
    for arg in sys.argv[3:]:
        k, _, v = arg.partition("=")
        filters[k] = v

    js = JS_TMPL % (json.dumps(filters), json.dumps(report_id), PAGE_SIZE, PAGE_SIZE)
    res = run(js)
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with open(out, "w") as f:
        json.dump(res, f)
    print(f"report={report_id} rows={len(res['rows'])} pages={res['pages']} total={res['total']} err={res['error']} -> {out}")
    if res["rows"]:
        print("keys:", list(res["rows"][0].keys())[:20])


if __name__ == "__main__":
    main()
