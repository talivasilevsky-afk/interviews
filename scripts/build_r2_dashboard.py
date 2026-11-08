#!/usr/bin/env python3
"""Build the Practice Pulse '26 round-2 dashboard from the three Formspree forms.

The round-2 survey runs as three versions (Agencies, Consulting, Architecture),
each posting to its own Formspree form. They are listed in dashboard/surveys.json
with the env var that holds each form's API key. This script:

  1. checks each key works (GET /api/0/forms/<id>/submissions → HTTP 200, ok),
  2. pulls every submission of every form (100 per page, offset paging),
  3. tags each submission with its survey (_source) and saves the raw data to
     data/formspree/<slug>.json (gitignored),
  4. writes practice-pulse-r2-dashboard.html (gitignored): one page with a
     Survey filter.

Page styles are copied from dashboard/template.html, so it looks like the
Stacked Marketing dashboard. Sessions whose email matches
scripts/test_email_hashes.txt are marked as tests.

Usage:
  python3 scripts/build_r2_dashboard.py                # fetch all forms and build
  python3 scripts/build_r2_dashboard.py --check        # only test the keys
  python3 scripts/build_r2_dashboard.py --from-cache   # rebuild from data/formspree/
"""
import argparse, datetime, hashlib, json, os, pathlib, re, sys, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG = ROOT / "dashboard" / "surveys.json"
TEMPLATE = ROOT / "dashboard" / "r2_template.html"
STYLE_SOURCE = ROOT / "dashboard" / "template.html"
CACHE = ROOT / "data" / "formspree"
OUT = ROOT / "practice-pulse-r2-dashboard.html"
TEST_HASHES = ROOT / "scripts" / "test_email_hashes.txt"
API = "https://formspree.io/api/0/forms/{form}/submissions?limit={limit}&offset={offset}"
PAGE = 100
DROP_FIELDS = {"_replyto", "_subject"}   # a copy of the email field, and the subject line


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
        raise FormspreeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from None
    except urllib.error.URLError as e:
        raise FormspreeError(f"network error: {e.reason}") from None
    if not body.get("ok", True) or "submissions" not in body:
        raise FormspreeError(f"unexpected response: {json.dumps(body)[:200]}")
    return body


def fetch_all(form_id, key):
    subs = []
    while True:
        page = get(form_id, key, len(subs))["submissions"]
        subs.extend(page)
        if len(page) < PAGE or len(subs) >= 100_000:
            return subs


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="only check that each key works")
    ap.add_argument("--from-cache", action="store_true", help="build from data/formspree/ instead of the API")
    ap.add_argument("--refresh-label", default="when the build script is run again")
    args = ap.parse_args()

    sources = json.loads(CONFIG.read_text())["sources"]
    failed, all_subs, as_of = False, [], datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for src in sources:
        name = f"{src['label']} ({src['form_id']})"
        cache = CACHE / f"{src['slug']}.json"
        try:
            if args.from_cache:
                raw = json.loads(cache.read_text())
                as_of = min(as_of, raw["asOf"])
                subs = raw["submissions"]
            else:
                key = os.environ.get(src["key_env"], "").strip()
                if not key:
                    raise FormspreeError(f"{src['key_env']} is not set")
                if args.check:
                    n = len(get(src["form_id"], key, 0)["submissions"])
                    print(f"{name}: key OK, {n}{'+' if n == PAGE else ''} submissions")
                    continue
                subs = fetch_all(src["form_id"], key)
                CACHE.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps({"asOf": as_of, "submissions": subs}, ensure_ascii=False, indent=1))
        except (FormspreeError, FileNotFoundError) as e:
            print(f"{name}: FAILED — {e}", file=sys.stderr)
            failed = True
            continue
        sessions = {s.get("session_id") for s in subs if s.get("session_id")}
        print(f"{name}: {len(subs)} rows, {len(sessions)} sessions")
        all_subs += [dict({k: v for k, v in s.items() if k not in DROP_FIELDS}, _source=src["slug"]) for s in subs]
    if args.check:
        sys.exit(1 if failed else 0)
    if failed:
        sys.exit("not building: a form could not be read, so the page would undercount")

    m = re.search(r"<style>.*?</style>", STYLE_SOURCE.read_text(), re.S)
    if not m:
        sys.exit("no <style> block in dashboard/template.html")
    page_sources = [{k: s.get(k) for k in ("slug", "label", "long", "form_id", "launch_at", "test_session_ids")} for s in sources]
    test_ids = test_session_ids(all_subs)
    html = TEMPLATE.read_text()
    swaps = {
        "/*__STYLE__*/": m.group(0),
        "/*__SOURCES__*/[]": js(page_sources),
        "/*__SNAPSHOT__*/null": js({"asOf": as_of, "submissions": all_subs}),
        "/*__REFRESH_LABEL__*/'when the build script is run again'": js(args.refresh_label),
        "/*__EMAIL_TEST_IDS__*/[]": js(test_ids),
    }
    for marker, value in swaps.items():
        if marker not in html:
            sys.exit(f"template marker missing: {marker}")
        html = html.replace(marker, value, 1)
    OUT.write_text(html)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(all_subs)} rows, {len(test_ids)} session(s) marked test by email, as of {as_of}")


if __name__ == "__main__":
    main()
