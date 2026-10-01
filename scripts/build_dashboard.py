#!/usr/bin/env python3
"""Build the Stacked Marketing survey dashboard from an Airtable export.

Input: a JSON file holding {"records": [...]}, either exactly as the Airtable
connector's list_records_for_table returns it (cellValuesByFieldId, select
values as objects) or in the compact form ("fields" keyed by field name,
select values as plain strings). Both are read by the page.

The report and invite email addresses are kept, because the Form activity tab
lists them with their session IDs. The page is visible to everyone it is
shared with. `_replyto` only duplicates them and is dropped.

Usage:
  python3 scripts/build_dashboard.py data/records.json \
      --as-of 2026-09-29T06:00:00Z --refresh-label "within two hours"
"""
import argparse, datetime, hashlib, json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "dashboard" / "template.html"
OUT = ROOT / "stacked-marketing-dashboard.html"
TEST_HASHES = ROOT / "scripts" / "test_email_hashes.txt"
SESSION_FIELD = "fldptlBmysZwmHkjz"

DROP_FIELDS = {
    "fldZm0HWRglCjD6Tv", "_subject",   # form subject line, not needed
    "flds2KmyjGSYlYy0c", "_replyto",   # duplicate of report_email / exit_email
}


def clean(rec):
    src_key = "cellValuesByFieldId" if "cellValuesByFieldId" in rec else "fields"
    cells = {}
    for k, v in (rec.get(src_key) or {}).items():
        if k in DROP_FIELDS:
            continue
        cells[k] = v
    return {"id": rec.get("id"), "createdTime": rec.get("createdTime"), src_key: cells}


def test_session_ids(records):
    """Session IDs where any email cell matches a hashed test address."""
    if not TEST_HASHES.exists():
        return []
    hashes = {l.strip() for l in TEST_HASHES.read_text().splitlines() if l.strip() and not l.startswith("#")}
    hits = set()
    for r in records:
        cells = r.get("cellValuesByFieldId") or r.get("fields") or {}
        sid = cells.get(SESSION_FIELD) or cells.get("session_id")
        for v in cells.values():
            if sid and isinstance(v, str) and "@" in v and hashlib.sha256(v.strip().lower().encode()).hexdigest() in hashes:
                hits.add(sid)
    return sorted(hits)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("records")
    ap.add_argument("--as-of", help="ISO time the rows were read (default: now, UTC)")
    ap.add_argument("--refresh-label", default="within two hours")
    args = ap.parse_args()

    data = json.loads(pathlib.Path(args.records).read_text())
    records = data["records"] if isinstance(data, dict) else data
    if not isinstance(records, list):
        sys.exit("records file must contain a list of records")
    as_of = args.as_of or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    snapshot = {"asOf": as_of, "records": [clean(r) for r in records]}
    blob = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    html = TEMPLATE.read_text()
    test_ids = test_session_ids(records)
    for marker in ("/*__SNAPSHOT__*/null", "/*__REFRESH_LABEL__*/'within two hours'", "/*__EMAIL_TEST_IDS__*/[]"):
        if marker not in html:
            sys.exit(f"template marker missing: {marker}")
    html = html.replace("/*__SNAPSHOT__*/null", blob, 1)
    html = html.replace("/*__REFRESH_LABEL__*/'within two hours'", json.dumps(args.refresh_label), 1)
    html = html.replace("/*__EMAIL_TEST_IDS__*/[]", json.dumps(test_ids), 1)
    OUT.write_text(html)

    sessions = {((r.get("cellValuesByFieldId") or r.get("fields") or {}).get("fldptlBmysZwmHkjz")
                 or (r.get("fields") or {}).get("session_id")) for r in records}
    sessions.discard(None)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(records)} rows, {len(sessions)} sessions, {len(test_ids)} marked test by email, as of {as_of}")


if __name__ == "__main__":
    main()
