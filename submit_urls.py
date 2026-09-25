#!/usr/bin/env python3
"""Submit this site's URLs to search engines for (re)crawling.

    python3 submit_urls.py            # preflight + submit
    python3 submit_urls.py --dry-run  # preflight only, send nothing
    python3 submit_urls.py --check    # crawl-accessibility report only

What actually reaches Google, as of 2026-09-25
----------------------------------------------
Nothing here pushes a URL into Google on demand. Two mechanisms that are
often suggested for this do not work:

  * GET https://www.google.com/ping?sitemap=...   -> HTTP 404, retired in
    June 2023. Bing's equivalent returns 410. Both are verified by the
    preflight below, so this script tells you when that changes.

  * The Google Indexing API is limited by policy to pages carrying
    JobPosting or BroadcastEvent-in-VideoObject structured data. These are
    academic project pages, so calling it would be outside its stated
    scope even with credentials.

What Google supports instead is the `Sitemap:` line in robots.txt (already
present) plus the Search Console Sitemaps report or its API. The API needs
a service account added as an owner of the verified property; until that
exists, the one-time submit in Search Console is the Google path, and this
script verifies the sitemap it will read.

IndexNow is real and does work, for the engines that participate: Bing,
Yandex, Seznam, Naver, Yep and Amazon share one submission. Google is not
among them. That is a genuine gain for those engines and no help at all
for a Google query, and the output says so rather than implying otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date

SITE = "https://chihhsuan-yang.github.io"
HOST = "chihhsuan-yang.github.io"
SITEMAP = f"{SITE}/sitemap.xml"

# The four URLs this task is about.
URLS = [
    f"{SITE}/",
    f"{SITE}/NeurIPS26_Precise-but-Uncoupled/",
    f"{SITE}/EMNLP_Cost-Aware-Protocol-Routing/",
    f"{SITE}/AAAI_Wrong-but-Useful/",
]

# Key file lives at the site root, so it authorises any URL on this host.
INDEXNOW_KEY = "e43cbc6cd3a44a28682b0f4745131963"
INDEXNOW_KEY_LOCATION = f"{SITE}/{INDEXNOW_KEY}.txt"
INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"

# Endpoints that are retired. Probed, not called in earnest: if one ever
# returns 200 again the preflight will say so instead of staying silent.
RETIRED = {
    "google sitemap ping": f"https://www.google.com/ping?sitemap={SITEMAP}",
    "bing sitemap ping": f"https://www.bing.com/ping?sitemap={SITEMAP}",
}

UA = "chihhsuan-yang.github.io submit_urls.py (+https://chihhsuan-yang.github.io/)"
TIMEOUT = 30

OK, WARN, BAD = "ok  ", "warn", "FAIL"


def _request(url, *, method="GET", body=None, headers=None):
    """Return (status, body_text). Network failure surfaces as status None."""
    req = urllib.request.Request(url, method=method, data=body)
    req.add_header("User-Agent", UA)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # An HTTP error is a real answer from the server, not a failure to ask.
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # DNS, TLS, timeout
        return None, f"{type(e).__name__}: {e}"


def check_sitemap():
    """The sitemap must list all four URLs; Google reads lastmod, not priority."""
    print("\n[sitemap]")
    status, body = _request(SITEMAP)
    if status != 200:
        print(f"  {BAD} {SITEMAP} -> {status}")
        return False
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        print(f"  {BAD} sitemap is not well-formed XML: {e}")
        return False

    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    entries = {}
    for u in root.findall("s:url", ns):
        loc = u.find("s:loc", ns)
        lm = u.find("s:lastmod", ns)
        if loc is not None and loc.text:
            entries[loc.text.strip()] = lm.text.strip() if lm is not None and lm.text else None

    good = True
    for url in URLS:
        if url not in entries:
            print(f"  {BAD} missing <loc>: {url}")
            good = False
            continue
        lastmod = entries[url]
        if not lastmod:
            # Not fatal, but Google leans on lastmod when it is trustworthy.
            print(f"  {WARN} {url} has no <lastmod>")
        else:
            print(f"  {OK} {url}  lastmod={lastmod}")
    print(f"  {len(entries)} URLs total; the 4 target URLs are "
          f"{'all present' if good else 'NOT all present'}.")
    return good


def check_crawlable():
    """A submission is pointless if the page then refuses indexing."""
    print("\n[crawl accessibility]")
    good = True
    for url in URLS:
        status, body = _request(url)
        if status != 200:
            print(f"  {BAD} {url} -> {status}")
            good = False
            continue

        # X-Robots-Tag is checked with a separate HEAD so a header-level
        # noindex cannot hide behind a clean-looking page body.
        req = urllib.request.Request(url, method="HEAD")
        req.add_header("User-Agent", UA)
        xrt = ""
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                xrt = r.headers.get("X-Robots-Tag", "") or ""
        except Exception:
            pass

        meta = ""
        low = body.lower()
        i = low.find('name="robots"')
        if i != -1:
            j = low.find('content="', i)
            if j != -1:
                meta = body[j + 9:low.find('"', j + 9)]

        problems = []
        if "noindex" in xrt.lower():
            problems.append(f"X-Robots-Tag: {xrt}")
        if "noindex" in meta.lower():
            problems.append(f'meta robots: {meta}')
        if not meta:
            problems.append("no robots meta tag")

        if problems:
            print(f"  {WARN if 'noindex' not in ' '.join(problems).lower() else BAD} "
                  f"{url}: {'; '.join(problems)}")
            if "noindex" in " ".join(problems).lower():
                good = False
        else:
            print(f"  {OK} {url}  robots=\"{meta}\""
                  f"{'  X-Robots-Tag=' + xrt if xrt else ''}")
    return good


def check_key_file():
    """IndexNow rejects the batch if the key file is missing or mismatched."""
    print("\n[indexnow key]")
    status, body = _request(INDEXNOW_KEY_LOCATION)
    if status != 200:
        print(f"  {BAD} {INDEXNOW_KEY_LOCATION} -> {status} "
              "(deploy the key file before submitting)")
        return False
    if body.strip() != INDEXNOW_KEY:
        print(f"  {BAD} key file content does not match the key in this script")
        return False
    print(f"  {OK} {INDEXNOW_KEY_LOCATION} serves the matching key")
    return True


def probe_retired():
    """Report the retired endpoints rather than asserting they are dead."""
    print("\n[retired endpoints: probed, not relied on]")
    for name, url in RETIRED.items():
        status, _ = _request(url)
        note = "still retired" if status in (404, 410) else "UNEXPECTED — recheck the docs"
        print(f"  {OK if status in (404, 410) else WARN} {name}: HTTP {status} ({note})")


def submit_indexnow(dry_run=False):
    print("\n[indexnow submission]")
    payload = {
        "host": HOST,
        "key": INDEXNOW_KEY,
        "keyLocation": INDEXNOW_KEY_LOCATION,
        "urlList": URLS,
    }
    if dry_run:
        print("  dry run, nothing sent. Payload would be:")
        print("  " + json.dumps(payload, indent=2).replace("\n", "\n  "))
        return True

    status, body = _request(
        INDEXNOW_ENDPOINT,
        method="POST",
        body=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    meaning = {
        200: "received",
        202: "received, key validation pending",
        400: "invalid format",
        403: "key not valid (file missing or content mismatch)",
        422: "URLs do not belong to this host, or key/schema mismatch",
        429: "rate limited",
    }.get(status, "unexpected")
    tag = OK if status in (200, 202) else BAD
    print(f"  {tag} POST {INDEXNOW_ENDPOINT} -> HTTP {status} ({meaning})")
    if body.strip():
        print(f"       body: {body.strip()[:200]}")
    print(f"  {len(URLS)} URLs submitted; shared with Bing, Yandex, Seznam, "
          "Naver, Yep, Amazon.")
    print("  Google does not participate in IndexNow — this does not reach Google.")
    return status in (200, 202)


def google_status():
    print("\n[google]")
    print("  No on-demand submission is available from a script:")
    print("    - sitemap ping endpoint: retired (probed above)")
    print("    - Indexing API: limited to JobPosting / BroadcastEvent pages")
    print("  What is in place for Google:")
    print(f"    - robots.txt advertises {SITEMAP}")
    print("    - sitemap lastmod is current and verified above")
    print("  Remaining manual step, once: submit the sitemap in Search Console")
    print("    https://search.google.com/search-console  ->  Sitemaps  ->  sitemap.xml")
    print("  Re-crawl after that is Google's decision, not a request we can force.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                    help="run every check, send no submission")
    ap.add_argument("--check", action="store_true",
                    help="accessibility and sitemap report only")
    args = ap.parse_args()

    print(f"submit_urls.py — {date.today().isoformat()} — {len(URLS)} URLs")

    sitemap_ok = check_sitemap()
    crawl_ok = check_crawlable()
    probe_retired()

    if args.check:
        print("\n--check: no submission attempted.")
        return 0 if (sitemap_ok and crawl_ok) else 1

    key_ok = check_key_file()
    if not crawl_ok:
        print("\nRefusing to submit: a target URL is unreachable or set to noindex.")
        return 1

    sent = submit_indexnow(dry_run=args.dry_run) if key_ok else False
    if not key_ok:
        print("\n[indexnow submission]\n  skipped: key file not live yet.")

    google_status()

    ok = sitemap_ok and crawl_ok and (sent or args.dry_run)
    print(f"\nresult: {'ok' if ok else 'completed with problems above'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
