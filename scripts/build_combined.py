#!/usr/bin/env python3
"""Build the combined research dashboard: every survey plus the interviews log.

Sources are listed in scripts/sources.json. Each survey entry names its Airtable
table, the local export (the raw list_records_for_table JSON), its launch time,
values to set on every row (Stacked rows are all agencies, so industry=agency)
and a map from its Airtable field IDs to the shared question names the page
uses. A new survey is a new entry there, not a new dashboard.

Rows are rewritten into the compact form ("fields" keyed by question name),
tagged with `source`, and their session IDs are prefixed with the source ID so
two surveys can never collide.

Email addresses and the full interview records (names, summaries, notes) are
kept in the page's snapshot so they can be pulled later, but the page does not
show them. `_replyto` / `_subject` only duplicate other fields and are dropped.

Usage:
  python3 scripts/build_combined.py --as-of 2026-10-25T12:00:00Z --refresh-label "by this afternoon"
"""
import argparse, datetime, hashlib, json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "scripts" / "sources.json"
TEMPLATE = ROOT / "dashboard" / "combined_template.html"
OUT = ROOT / "combined-research-dashboard.html"
TEST_HASHES = ROOT / "scripts" / "test_email_hashes.txt"
DROP = {"_replyto", "_subject"}

# Interview log fields are free text; bucket them into the survey's vocabulary.
SIZE_BUCKETS = [(1, "1"), (2, "2"), (5, "3-5"), (10, "6-10"), (16, "11-16"), (24, "17-24"), (50, "25-50"), (100, "51-100")]


def load_records(path):
    data = json.loads((ROOT / path).read_text())
    records = data["records"] if isinstance(data, dict) else data
    if not isinstance(records, list):
        sys.exit(f"{path}: records file must contain a list of records")
    total = data.get("metadata", {}).get("totalRecordCount") if isinstance(data, dict) else None
    if total is not None and total != len(records):
        sys.exit(f"{path}: {len(records)} rows but Airtable reports {total}; fetch every page first")
    return records


def cells_of(rec):
    return rec.get("cellValuesByFieldId") or rec.get("fields") or {}


def test_hashes():
    if not TEST_HASHES.exists():
        return set()
    return {l.strip() for l in TEST_HASHES.read_text().splitlines() if l.strip() and not l.startswith("#")}


def survey_rows(src, hashes):
    fmap = src["fields"]
    rows, test_ids = [], set()
    for rec in load_records(src["data"]):
        fields = {}
        for k, v in cells_of(rec).items():
            name = fmap.get(k, k)
            if name not in DROP:
                fields[name] = v
        sid = fields.get("session_id")
        if sid:
            sid = f"{src['id']}:{str(sid).strip()}"
            fields["session_id"] = sid
            for v in fields.values():
                if isinstance(v, str) and "@" in v and hashlib.sha256(v.strip().lower().encode()).hexdigest() in hashes:
                    test_ids.add(sid)
        fields.update(src.get("set") or {})
        fields["source"] = src["id"]
        rows.append({"id": rec.get("id"), "createdTime": rec.get("createdTime"), "fields": fields})
    return rows, test_ids


def bucket_size(n):
    if not isinstance(n, (int, float)) or n <= 0:
        return None
    for top, key in SIZE_BUCKETS:
        if n <= top:
            return key
    return "100+"


def bucket_country(text):
    t = (text or "").strip()
    low = t.lower()
    if not t or low.startswith("not enough"):
        return None
    if re.match(r"^(us|u\.s\.|usa)\b", low) or "united states" in low:
        return "United States"
    if low.startswith("israel"):
        return "Israel"
    return re.split(r"[\s(/,;—-]", t, maxsplit=1)[0].strip().title() or None


def bucket_vertical(text):
    low = (text or "").lower()
    if not low.strip():
        return None
    if "architect" in low or "engineering" in low or "interior" in low:
        return "arch"
    if any(w in low for w in ("agency", "marketing", "advertis", " pr ", "public relations", "communications")):
        return "agency"
    if "design" in low:
        return "design"
    if any(w in low for w in ("cfo", "finance", "accounting", "wealth", "bookkeep")):
        return "finance"
    if "consult" in low:
        return "consult"
    return "other"


def bucket_partner(text):
    low = (text or "").strip().lower()
    if not low:
        return None
    if low.startswith(("yes", "strong yes")):
        return "yes"
    if low.startswith("hedged"):
        return "maybe"
    if low.startswith("not asked") or (low.startswith("no") and "not asked" in low[:30]):
        return "not_asked"
    if low.startswith("no"):
        return "no"
    return "other"


def bucket_icp(text):
    low = (text or "").strip().lower()
    if low.startswith("yes"):
        return "yes"
    if low.startswith("partial"):
        return "partial"
    if low.startswith("no"):
        return "no"
    return None


def interview_rows(cfg):
    f = cfg["fields"]
    raw = load_records(cfg["data"])
    out = []
    for rec in raw:
        c = cells_of(rec)
        size = c.get(f["team_size"])
        out.append({
            "id": rec.get("id"),
            "date": c.get(f["date"]) or (rec.get("createdTime") or "")[:10] or None,
            "country": bucket_country(c.get(f["country"])),
            "industry": bucket_vertical(c.get(f["vertical"])),
            "team_size": bucket_size(size),
            "employees": size if isinstance(size, (int, float)) else None,
            "icp": bucket_icp(c.get(f["icp"])),
            "partner": bucket_partner(c.get(f["design_partner"])),
        })
    return out, raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", help="ISO time the rows were read (default: now, UTC)")
    ap.add_argument("--refresh-label", default="by this afternoon")
    args = ap.parse_args()
    as_of = args.as_of or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    cfg = json.loads(CONFIG.read_text())
    hashes = test_hashes()
    records, test_ids, summary = [], set(), []
    for src in cfg["surveys"]:
        rows, ids = survey_rows(src, hashes)
        records += rows
        test_ids |= ids
        sessions = {r["fields"].get("session_id") for r in rows} - {None}
        summary.append(f"{src['id']} {len(rows)} rows / {len(sessions)} sessions")
    interviews, interviews_raw = interview_rows(cfg["interviews"])

    snapshot = {
        "asOf": as_of,
        "sources": [{"id": s["id"], "label": s["label"], "launch": s.get("launch"),
                     "baseId": s["baseId"], "tableId": s["tableId"]} for s in cfg["surveys"]],
        "records": records,
        "interviews": interviews,
        "interviewsRaw": interviews_raw,   # stored for later use, never rendered
    }
    blob = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    html = TEMPLATE.read_text()
    for marker in ("/*__SNAPSHOT__*/null", "/*__REFRESH_LABEL__*/'by this afternoon'", "/*__EMAIL_TEST_IDS__*/[]"):
        if marker not in html:
            sys.exit(f"template marker missing: {marker}")
    html = html.replace("/*__SNAPSHOT__*/null", blob, 1)
    html = html.replace("/*__REFRESH_LABEL__*/'by this afternoon'", json.dumps(args.refresh_label), 1)
    html = html.replace("/*__EMAIL_TEST_IDS__*/[]", json.dumps(sorted(test_ids)), 1)
    OUT.write_text(html)
    print(f"wrote {OUT.relative_to(ROOT)}: {'; '.join(summary)}; {len(interviews)} interviews; "
          f"{len(test_ids)} marked test by email; as of {as_of}")


if __name__ == "__main__":
    main()
