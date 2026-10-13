#!/usr/bin/env python3
"""Build the Stacked Marketing form-activity dashboard (no email addresses).

Input: the same Airtable export build_dashboard.py reads, {"records": [...]},
kept in the gitignored data/ folder.

Session logic is not redefined here. The session code (rowFromRecord,
mergeSessions, the launch cutoff and the hand-marked test IDs) and the
screen order and labels are taken from dashboard/template.html at build time
and run with Node over email-free records. Test detection reuses
build_dashboard.test_session_ids (hashed emails vs scripts/test_email_hashes.txt).

Only per-session flags are embedded in the page: id, firstTs, isTest,
screenedOut, complete, lastScreen, reportOptIn, exitOptIn, calendly.
The build fails if the output contains anything that looks like an email.

Usage:
  python3 scripts/build_form_activity.py data/records.json \
      --as-of 2026-10-13T06:00:00Z --refresh-label "within two hours"
"""
import argparse, datetime, json, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_dashboard import test_session_ids  # noqa: E402  (one definition of hash-based test detection)

FULL_TEMPLATE = ROOT / "dashboard" / "template.html"
TEMPLATE = ROOT / "dashboard" / "form_activity_template.html"
OUT = ROOT / "form-activity-dashboard.html"

# Email fields: report_email, exit_email, meeting_email, _replyto (plus _subject, unused).
EMAIL_FIELDS = {
    "fldc3wZZ71PFwmLio": "report_email", "flddFPQlT8haxZJba": "exit_email",
    "fld6tBkNeKUBRqsFW": "meeting_email", "flds2KmyjGSYlYy0c": "_replyto",
}
DROP_FIELDS = {"fldZm0HWRglCjD6Tv", "_subject"}
# report_email / exit_email only count as "left an email"; keep that fact, not the address.
PRESENCE = "provided"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")

# Taken verbatim from dashboard/template.html.
SESSION_CODE = ["const FIELD_IDS", "const MULTI_FIELDS", "const ANSWER_FIELDS", "const LAUNCH_AT", "const LAUNCH_LABEL",
                "const SHOW_TESTS_BY_DEFAULT", "const TEST_SESSION_IDS",
                "function hasVal", "function normVal", "function rowFromRecord", "function mergeSessions"]
PAGE_CODE = ["const LAUNCH_AT", "const LAUNCH_LABEL", "const SHOW_TESTS_BY_DEFAULT", "const SCREEN_ORDER", "const SCREEN_LABEL",
             "function esc", "function hasVal", "function pct", "function el", "function fmtTime",
             "function renderBarList", "function hlCard"]


def extract(src, head):
    """Return the top-level statement starting with `head`. Top-level code in template.html starts at
    column 0 and its body is indented, so a statement ends at the first column-0 line that closes it."""
    lines = src.split("\n")
    for i, line in enumerate(lines):
        if re.match(re.escape(head) + r"\b", line):
            break
    else:
        sys.exit(f"not found in template.html: {head}")
    out = []
    for j in range(i, len(lines)):
        line = lines[j]
        if j > i and line[:1] not in (" ", "\t", ")", "]", "}"):
            sys.exit(f"could not find the end of {head}")
        out.append(line)
        code = re.sub(r"\s*//[^'\"`]*$", "", line).rstrip()
        if head.startswith("function"):
            if (j == i and code.endswith("}")) or (j > i and code == "}"):
                return "\n".join(out)
        elif code.endswith(";") and (j == i or line[:1] != " "):
            return "\n".join(out)
    sys.exit(f"could not parse {head}")


def strip_emails(rec):
    """Copy a record without any email address. Opt-in fields keep only a presence marker."""
    src_key = "cellValuesByFieldId" if "cellValuesByFieldId" in rec else "fields"
    cells = {}
    for k, v in (rec.get(src_key) or {}).items():
        if k in DROP_FIELDS:
            continue
        if k in EMAIL_FIELDS or k in EMAIL_FIELDS.values():
            name = EMAIL_FIELDS.get(k, k)
            if name in ("report_email", "exit_email") and isinstance(v, str) and v.strip():
                cells[k] = PRESENCE
            continue
        if isinstance(v, str) and "@" in v:
            v = "[removed]"      # free text that may hold an address; not used for form activity
        cells[k] = v
    return {"id": rec.get("id"), "createdTime": rec.get("createdTime"), src_key: cells}


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

    # Every address in the export, kept only in memory to prove none reach the page.
    addresses = {m.lower() for r in records for v in (r.get("cellValuesByFieldId") or r.get("fields") or {}).values()
                 if isinstance(v, str) for m in EMAIL_RE.findall(v)}

    test_ids = test_session_ids(records)
    clean = [strip_emails(r) for r in records]
    del records, data

    full = FULL_TEMPLATE.read_text()
    session_js = "\n".join(extract(full, h) for h in SESSION_CODE)
    if "/*__EMAIL_TEST_IDS__*/[]" not in session_js:
        sys.exit("template marker missing: /*__EMAIL_TEST_IDS__*/[]")
    session_js = session_js.replace("/*__EMAIL_TEST_IDS__*/[]", json.dumps(test_ids), 1)
    driver = session_js + """
const recs = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const { sessions } = mergeSessions(recs.map(rowFromRecord));
process.stdout.write(JSON.stringify(sessions.map(s => ({
  id: s.id, firstTs: s.firstTs, isTest: s.isTest, screenedOut: s.screenedOut, complete: s.complete,
  lastScreen: s.lastScreen, reportOptIn: s.reportOptIn, exitOptIn: s.exitOptIn, calendly: s.calendly,
}))));
"""
    res = subprocess.run(["node", "-e", driver], input=json.dumps(clean), capture_output=True, text=True)
    if res.returncode:
        sys.exit("session logic failed:\n" + res.stderr[-2000:])
    sessions = json.loads(res.stdout)
    allowed = {"id", "firstTs", "isTest", "screenedOut", "complete", "lastScreen", "reportOptIn", "exitOptIn", "calendly"}
    for s in sessions:
        if set(s) - allowed:
            sys.exit("unexpected session keys: " + ", ".join(sorted(set(s) - allowed)))

    snapshot = {"asOf": as_of, "sessions": sorted(sessions, key=lambda s: s["firstTs"])}
    blob = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page_js = "\n".join(extract(full, h) for h in PAGE_CODE)
    style = re.search(r"<style>.*?</style>", full, re.S).group(0)

    html = TEMPLATE.read_text()
    for marker in ("<!--__STYLE__-->", "/*__SHARED__*/", "/*__SNAPSHOT__*/null", "/*__REFRESH_LABEL__*/'within two hours'"):
        if marker not in html:
            sys.exit(f"template marker missing: {marker}")
    html = html.replace("<!--__STYLE__-->", style, 1)
    html = html.replace("/*__SHARED__*/", page_js, 1)
    html = html.replace("/*__SNAPSHOT__*/null", blob, 1)
    html = html.replace("/*__REFRESH_LABEL__*/'within two hours'", json.dumps(args.refresh_label), 1)

    # Privacy gate: no address-like text, no address from the export, and "@" only in CSS at-rules.
    low = html.lower()
    leaks = EMAIL_RE.findall(html)
    if leaks or any(a in low for a in addresses):
        sys.exit(f"refusing to write: {len(leaks) or 'some'} email-like strings in the page")
    stray = [m.start() for m in re.finditer("@", html) if not html.startswith("@media", m.start())]
    if stray:
        sys.exit(f"refusing to write: {len(stray)} '@' characters outside @media rules")
    OUT.write_text(html)

    vis = [s for s in sessions if not s["isTest"]]
    done = sum(1 for s in vis if not s["screenedOut"] and s["complete"])
    ex = sum(1 for s in vis if s["screenedOut"])
    print(f"wrote {OUT.relative_to(ROOT)}: {len(clean)} rows, {len(sessions)} sessions "
          f"({len(sessions) - len(vis)} test, {len(test_ids)} by email hash); without tests: "
          f"{done} completed, {len(vis) - done - ex} partial, {ex} exited; "
          f"@media rules only: {html.count('@media')}; email-like strings: 0; as of {as_of}")


if __name__ == "__main__":
    main()
