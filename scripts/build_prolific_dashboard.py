#!/usr/bin/env python3
"""Build the Prolific Survey dashboard from an Airtable export.

Input: a JSON file holding {"records": [...]}, either exactly as the Airtable
connector's list_records_for_table returns it (cellValuesByFieldId) or in the
compact form ("fields" keyed by field name). Both are read by the page.

Source table: "Prolific responses" (base appo5swnwzMKInvl5, table tbltbSUKIxPHTXQTD).

The report, invite and meeting email addresses are kept, because the Form
activity and Interviews tabs list them with their session IDs. The page is
visible to everyone it is shared with. `_replyto` only duplicates them and is
dropped.

Usage:
  python3 scripts/build_prolific_dashboard.py data/prolific-records.json \
      --as-of 2026-10-01T12:00:00Z --refresh-label "within two hours"
"""
import argparse, datetime, hashlib, json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "dashboard" / "prolific_template.html"
OUT = ROOT / "prolific-survey-dashboard.html"
TEST_HASHES = ROOT / "scripts" / "test_email_hashes.txt"
SESSION_FIELD = "fldOHONlVP5gVwwYP"
PID_FIELD = "fldH95uiuCP9GVRSM"

DROP_FIELDS = {
    "fldfny4oMYbbmy1Bn", "_subject",   # form subject line, not needed
    "fldAJnzV4herIpZhD", "_replyto",   # duplicate of report / exit / meeting email
}


def cells_of(rec):
    return rec.get("cellValuesByFieldId") or rec.get("fields") or {}


def clean(rec):
    src_key = "cellValuesByFieldId" if "cellValuesByFieldId" in rec else "fields"
    cells = {k: v for k, v in (rec.get(src_key) or {}).items() if k not in DROP_FIELDS}
    return {"id": rec.get("id"), "createdTime": rec.get("createdTime"), src_key: cells}


def session_of(cells):
    return cells.get(SESSION_FIELD) or cells.get("session_id")


def test_session_ids(records):
    """Session IDs where any email cell matches a hashed test address."""
    if not TEST_HASHES.exists():
        return []
    hashes = {l.strip() for l in TEST_HASHES.read_text().splitlines() if l.strip() and not l.startswith("#")}
    hits = set()
    for r in records:
        cells = cells_of(r)
        sid = session_of(cells)
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

    sessions = {session_of(cells_of(r)) for r in records} - {None}
    pids = {cells_of(r).get(PID_FIELD) or cells_of(r).get("prolific_pid") for r in records} - {None, ""}
    print(f"wrote {OUT.relative_to(ROOT)}: {len(records)} rows, {len(sessions)} sessions, "
          f"{len(pids)} Prolific IDs, {len(test_ids)} marked test by email, as of {as_of}")


if __name__ == "__main__":
    main()
