#!/usr/bin/env python3
"""Build one dashboard per Formspree survey, reading submissions from the Formspree API.

Surveys are listed in dashboard/surveys.json (form ID, the env var holding its
API key, title, filters, optional question wording and answer labels). For each
survey this script:

  1. checks the key works (GET /api/0/forms/<id>/submissions, HTTP 200 + ok),
  2. pulls every submission (100 per page, offset paging),
  3. saves the raw response to data/formspree/<slug>.json (gitignored),
  4. writes <slug>-survey-dashboard.html at the repo root (gitignored).

The page styles are copied from dashboard/template.html, so these dashboards
look the same as the Stacked Marketing one.

Usage:
  python3 scripts/build_formspree_dashboards.py                 # all surveys
  python3 scripts/build_formspree_dashboards.py consultants     # just one
  python3 scripts/build_formspree_dashboards.py --check         # only test the keys
  python3 scripts/build_formspree_dashboards.py --from-cache    # rebuild from data/formspree/
"""
import argparse, datetime, hashlib, json, os, pathlib, re, sys, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "dashboard" / "surveys.json"
TEMPLATE = ROOT / "dashboard" / "formspree_template.html"
STYLE_SOURCE = ROOT / "dashboard" / "template.html"
CACHE = ROOT / "data" / "formspree"
TEST_HASHES = ROOT / "scripts" / "test_email_hashes.txt"
API = "https://formspree.io/api/0/forms/{form}/submissions?limit={limit}&offset={offset}"
PAGE = 100
DROP_FIELDS = {"_replyto", "_subject"}   # duplicates of the email field / the subject line


class FormspreeError(Exception):
    pass


def get(form_id, key, offset):
    req = urllib.request.Request(API.format(form=form_id, limit=PAGE, offset=offset),
                                 headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                                          "User-Agent": "thinkup-survey-dashboard"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise FormspreeError(f"HTTP {e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise FormspreeError(f"network error: {e.reason}") from None
    if not body.get("ok", True) or "submissions" not in body:
        raise FormspreeError(f"unexpected response: {json.dumps(body)[:200]}")
    return body


def fetch_all(form_id, key):
    first = get(form_id, key, 0)
    fields, subs = list(first.get("fields") or []), list(first["submissions"])
    page = first["submissions"]
    while len(page) == PAGE and len(subs) < 100_000:
        page = get(form_id, key, len(subs))["submissions"]
        subs.extend(page)
    return fields, subs


def test_session_ids(subs):
    if not TEST_HASHES.exists():
        return []
    hashes = {l.strip() for l in TEST_HASHES.read_text().splitlines() if l.strip() and not l.startswith("#")}
    hits = set()
    for s in subs:
        sid = s.get("session_id")
        for v in s.values():
            if sid and isinstance(v, str) and "@" in v and hashlib.sha256(v.strip().lower().encode()).hexdigest() in hashes:
                hits.add(sid)
    return sorted(hits)


def js(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def build(survey, fields, subs, as_of, refresh_label, style):
    subs = [{k: v for k, v in s.items() if k not in DROP_FIELDS} for s in subs]
    fields = [f for f in fields if f not in DROP_FIELDS]
    html = TEMPLATE.read_text()
    page_cfg = {k: survey.get(k) for k in ("slug", "form_id", "eyebrow", "title", "subtitle", "launch_at", "kinds",
                                           "test_session_ids", "filters", "questions", "labels")}
    test_ids = test_session_ids(subs)
    swaps = {
        "/*__TITLE__*/Survey": survey["title"].replace("<", "&lt;"),
        "/*__STYLE__*/": style,
        "/*__CONFIG__*/{}": js(page_cfg),
        "/*__SNAPSHOT__*/null": js({"asOf": as_of, "fields": fields, "submissions": subs}),
        "/*__REFRESH_LABEL__*/'when the build script is run again'": js(refresh_label),
        "/*__EMAIL_TEST_IDS__*/[]": js(test_ids),
    }
    for marker, value in swaps.items():
        if marker not in html:
            sys.exit(f"template marker missing: {marker}")
        html = html.replace(marker, value, 1)
    out = ROOT / f"{survey['slug']}-survey-dashboard.html"
    out.write_text(html)
    sessions = {s.get("session_id") for s in subs if s.get("session_id")}
    return out, len(sessions), len(test_ids)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("slugs", nargs="*", help="survey slugs from dashboard/surveys.json (default: all)")
    ap.add_argument("--check", action="store_true", help="only check that each key works")
    ap.add_argument("--from-cache", action="store_true", help="build from data/formspree/<slug>.json instead of the API")
    ap.add_argument("--refresh-label", default="when the build script is run again")
    args = ap.parse_args()

    surveys = json.loads(CONFIG.read_text())["surveys"]
    if args.slugs:
        unknown = set(args.slugs) - {s["slug"] for s in surveys}
        if unknown:
            sys.exit(f"unknown survey: {', '.join(sorted(unknown))}")
        surveys = [s for s in surveys if s["slug"] in args.slugs]

    m = re.search(r"<style>.*?</style>", STYLE_SOURCE.read_text(), re.S)
    if not m:
        sys.exit("no <style> block in dashboard/template.html")
    style = m.group(0)

    failed = False
    for sv in surveys:
        name = f"{sv['slug']} ({sv['form_id']})"
        cache = CACHE / f"{sv['slug']}.json"
        try:
            if args.from_cache:
                raw = json.loads(cache.read_text())
            else:
                key = os.environ.get(sv["key_env"], "").strip()
                if not key:
                    raise FormspreeError(f"{sv['key_env']} is not set")
                if args.check:
                    body = get(sv["form_id"], key, 0)
                    print(f"{name}: key OK, {len(body['submissions'])}{'+' if len(body['submissions']) == PAGE else ''} submissions")
                    continue
                fields, subs = fetch_all(sv["form_id"], key)
                raw = {"asOf": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "fields": fields, "submissions": subs}
                CACHE.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(raw, ensure_ascii=False, indent=1))
        except (FormspreeError, FileNotFoundError) as e:
            print(f"{name}: FAILED — {e}", file=sys.stderr)
            failed = True
            continue
        out, n_sessions, n_tests = build(sv, raw["fields"], raw["submissions"], raw["asOf"], args.refresh_label, style)
        print(f"{name}: wrote {out.relative_to(ROOT)} — {len(raw['submissions'])} rows, {n_sessions} sessions, "
              f"{n_tests} marked test by email, as of {raw['asOf']}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
