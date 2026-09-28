#!/usr/bin/env python3
"""Build the Stacked Marketing survey dashboard from an Airtable export.

Input: a JSON file holding {"records": [...]}, either exactly as the Airtable
connector's list_records_for_table returns it (cellValuesByFieldId, select
values as objects) or in the compact form ("fields" keyed by field name,
select values as plain strings). Both are read by the page.

Email addresses are replaced with `true` before anything is embedded, so the
published page only knows whether someone left an email, never the address.

Usage:
  python3 scripts/build_dashboard.py data/records.json \
      --as-of 2026-09-29T06:00:00Z --refresh-label "9:00 Israel time"
"""
import argparse, datetime, json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "dashboard" / "template.html"
OUT = ROOT / "stacked-marketing-dashboard.html"

EMAIL_FIELDS = {
    # by field id and by field name
    "fldc3wZZ71PFwmLio", "report_email",
    "flddFPQlT8haxZJba", "exit_email",
    "flds2KmyjGSYlYy0c", "_replyto",
}
DROP_FIELDS = {"fldZm0HWRglCjD6Tv", "_subject"}  # form subject line, not needed


def clean(rec):
    src_key = "cellValuesByFieldId" if "cellValuesByFieldId" in rec else "fields"
    cells = {}
    for k, v in (rec.get(src_key) or {}).items():
        if k in DROP_FIELDS:
            continue
        if k in EMAIL_FIELDS:
            v = bool(v)
        cells[k] = v
    return {"id": rec.get("id"), "createdTime": rec.get("createdTime"), src_key: cells}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("records")
    ap.add_argument("--as-of", help="ISO time the rows were read (default: now, UTC)")
    ap.add_argument("--refresh-label", default="9:00")
    args = ap.parse_args()

    data = json.loads(pathlib.Path(args.records).read_text())
    records = data["records"] if isinstance(data, dict) else data
    if not isinstance(records, list):
        sys.exit("records file must contain a list of records")
    as_of = args.as_of or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    snapshot = {"asOf": as_of, "records": [clean(r) for r in records]}
    blob = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    html = TEMPLATE.read_text()
    for marker in ("/*__SNAPSHOT__*/null", "/*__REFRESH_LABEL__*/'9:00'"):
        if marker not in html:
            sys.exit(f"template marker missing: {marker}")
    html = html.replace("/*__SNAPSHOT__*/null", blob, 1)
    html = html.replace("/*__REFRESH_LABEL__*/'9:00'", json.dumps(args.refresh_label), 1)
    OUT.write_text(html)

    sessions = {((r.get("cellValuesByFieldId") or r.get("fields") or {}).get("fldptlBmysZwmHkjz")
                 or (r.get("fields") or {}).get("session_id")) for r in records}
    sessions.discard(None)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(records)} rows, {len(sessions)} sessions, as of {as_of}")


if __name__ == "__main__":
    main()
