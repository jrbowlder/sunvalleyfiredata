#!/usr/bin/env python3
"""Pull incident type and address for the NERIS era from Elite's NERIS incident list.

Why this exists: the NERIS-era CSV export (`elite_neris_incident_fact.csv`) carries no
address column and no usable incident type (its type column is the constant "Fire NERIS").
Both exist in Elite - the NERIS Fire incident list shows `NerisFullAddress` and
`Primary Incident Type` - so this script drives that list in the already-logged-in browser
and saves (incident_number, primary_incident_type, full_address).

Usage (browser must be open on port 9222, logged into Elite):

  python3 etl/pull/elite_neris_addresses.py

Writes data/raw/elite_neris_addresses.csv. Note the list's default filter is the last 60
days, which is why the script widens it to the start of the NERIS era before paging.

The list shows NERIS *Fire* incidents only; that is the whole population for this
department. If a future export gains an address column, drop this script.
"""
import csv
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "data", "raw", "elite_neris_incidents.csv")
LIST_URL = ("https://idaho.imagetrendelite.com/Elite/Organizationidaho/Agency0jt21u8bwr"
            "/RunForm/IncidentList?startingFilter=neris")
MATCH = "imagetrend"

WIDEN = """(() => {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
  const [from, to] = [...document.querySelectorAll("input[type=date]")];
  if (from) { setter.call(from, "2025-01-01"); from.dispatchEvent(new Event("change",{bubbles:true})); }
  if (to)   { setter.call(to, new Date().toISOString().slice(0,10)); to.dispatchEvent(new Event("change",{bubbles:true})); }
  const go = [...document.querySelectorAll("button,a,input[type=submit]")]
    .find(e => /^Go$/i.test((e.innerText || e.value || "").trim()));
  if (go) go.click();
  return "widened";
})()"""

PAGE_SIZE = """(() => {
  const ok = [];
  for (const id of ["page-size-upper", "page-size-lower"]) {
    const s = document.getElementById(id);
    if (s) { s.value = "200"; s.dispatchEvent(new Event("change", {bubbles: true})); ok.push(id); }
  }
  return ok.join(",") || "no page-size select";
})()"""

EXTRACT = """(() => {
  const rows = [...document.querySelectorAll("tr")]; const out = [];
  for (const r of rows) {
    const c = [...r.children].map(x => x.innerText.replace(/\\s+/g, " ").trim());
    if (c.length >= 7 && /^[0-9]{2}-[0-9]+$|^SVFD[0-9]+$|^BC[0-9-]+$/.test(c[4] || "")) {
      out.push({ num: c[4], type: c[5] || "", addr: c[6] || "", status: c[2] || "", created: c[3] || "" });
    }
  }
  return JSON.stringify(out);
})()"""

NEXT = """(() => {
  const btns = [...document.querySelectorAll("button[aria-label='Next page']")]
    .filter(b => b.offsetParent !== null);
  if (!btns.length) return "none";
  btns[btns.length - 1].click();
  return "clicked";
})()"""


def main() -> None:
    cdp.evaluate(f"(() => {{ location.href = {LIST_URL!r}; return 1; }})()", MATCH)
    time.sleep(10)
    print(cdp.evaluate(WIDEN, MATCH))
    time.sleep(10)
    print(cdp.evaluate(PAGE_SIZE, MATCH))
    time.sleep(6)

    seen: dict[str, dict] = {}
    for page in range(1, 12):
        raw = cdp.evaluate(EXTRACT, MATCH)
        rows = json.loads(raw) if isinstance(raw, str) else raw
        for r in rows:
            seen.setdefault(r["num"], r)
        print(f"  page {page}: {len(rows)} rows, {len(seen)} unique", flush=True)
        if cdp.evaluate(NEXT, MATCH) != "clicked":
            break
        time.sleep(6)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["incident_number", "primary_incident_type", "full_address",
                    "status", "call_created"])
        for r in seen.values():
            w.writerow([r["num"], r.get("type", ""), r["addr"], r["status"], r["created"]])
    with_addr = sum(1 for r in seen.values() if r["addr"].strip())
    print(f"  wrote {len(seen)} incidents ({with_addr} with an address) -> {OUT}")


if __name__ == "__main__":
    main()
