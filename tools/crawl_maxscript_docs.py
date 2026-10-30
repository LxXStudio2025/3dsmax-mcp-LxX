"""Crawl the 3ds Max 2025 MAXScript Reference into local markdown.

Why 2025 and not 2016: cloudhelp/2016 returns 404/403 for every path (the
release is retired), so 2016 can only be reached through the SPA at
view/3DSMAX/2016 which needs a full browser render per page (~5 s x ~2000
pages). The 2025 reference is plain static HTML under cloudhelp/.../files/ and
crawls at ~0.3 s/page concurrently.

2016-specific differences are covered by max_api / max_api_search against the
live process, which is authoritative anyway.

Usage: python crawl_maxscript_docs.py [--limit N]
Output: docs/maxscript-2025/<slug>.md plus index.json
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures as futures
import html
import json
import os
import re
import threading
import time
import urllib.request

VERSION = "2025"
BASE = f"https://help.autodesk.com/cloudhelp/{VERSION}/ENU/MAXScript-Help/files/"
SEED = "GUID-6FC81BE7-58FF-4C63-8362-0BDCFA9F904C.html"  # MAXScript Language Reference
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
OUT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "docs", f"maxscript-{VERSION}"
)

_lock = threading.Lock()
seen: set[str] = set()
done = {"pages": 0, "errors": 0, "bytes": 0}


def extract(html_text: str) -> tuple[str, str]:
    """Return (title, plain text) from a cloudhelp page."""
    title = ""
    m = re.search(r"<title>(.*?)</title>", html_text, re.S | re.I)
    if m:
        title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
    # drop chrome that carries no documentation value
    body = re.sub(r"<script.*?</script>", " ", html_text, flags=re.S | re.I)
    body = re.sub(r"<style.*?</style>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<nav.*?</nav>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<header.*?</header>", " ", body, flags=re.S | re.I)
    body = re.sub(r"<footer.*?</footer>", " ", body, flags=re.S | re.I)
    main = re.search(r"<main.*?>(.*?)</main>", body, re.S | re.I)
    if main:
        body = main.group(1)
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    body = re.sub(r"</(p|div|li|tr|h[1-6]|pre)>", "\n", body, flags=re.I)
    body = re.sub(r"<[^>]+>", " ", body)
    text = html.unescape(body)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(ln for ln in lines if ln)
    return title, text.strip()


def slug_of(rel: str) -> str:
    base = rel.rsplit("/", 1)[-1]
    return base.replace(".html", "")


def fetch_one(rel: str) -> tuple[str, str, str, list[str]] | None:
    url = BASE + rel
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        raw = urllib.request.urlopen(req, timeout=25).read()
    except Exception:
        with _lock:
            done["errors"] += 1
        return None
    page = raw.decode("utf-8", "replace")
    title, text = extract(page)
    children = []
    for m in re.findall(r'href="([^"]*GUID-[0-9A-Fa-f-]+\.html)"', page):
        # keep subdirectories; resolve relative ./ segments
        child = os.path.normpath(os.path.join(os.path.dirname(rel), m)).replace(
            "\\", "/"
        )
        if child.startswith("../"):
            child = child.split("../", 1)[1]
        children.append(child)
    return rel, title, text, children


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=4000)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    queue: collections.deque[str] = collections.deque([SEED])
    seen.add(SEED)
    index: dict[str, dict] = {}
    t0 = time.time()
    last_report = 0.0

    with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        inflight: dict[futures.Future, str] = {}

        def submit_next() -> bool:
            while queue and len(inflight) < args.workers * 2 and len(seen) < args.limit:
                rel = queue.popleft()
                inflight[pool.submit(fetch_one, rel)] = rel
            return bool(inflight)

        submit_next()
        while inflight:
            finished, _ = futures.wait(inflight, return_when=futures.FIRST_COMPLETED)
            for fut in finished:
                rel = inflight.pop(fut)
                result = fut.result()
                if result is None:
                    continue
                rel, title, text, children = result
                with _lock:
                    done["pages"] += 1
                    done["bytes"] += len(text)
                slug = slug_of(rel)
                with open(
                    os.path.join(OUT_DIR, slug + ".md"), "w", encoding="utf-8"
                ) as fh:
                    fh.write(f"# {title or slug}\n\n")
                    fh.write(f"<!-- source: {BASE}{rel} -->\n\n")
                    fh.write(text + "\n")
                index[slug] = {"title": title or slug, "path": rel, "chars": len(text)}
                for child in children:
                    if child not in seen and len(seen) < args.limit:
                        seen.add(child)
                        queue.append(child)
            submit_next()
            now = time.time()
            if now - last_report > 5:
                last_report = now
                with _lock:
                    print(
                        f"  pages={done['pages']} errors={done['errors']} "
                        f"queued={len(queue)} known={len(seen)} "
                        f"{(time.time() - t0):.0f}s",
                        flush=True,
                    )

    with open(os.path.join(OUT_DIR, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=1, ensure_ascii=False, sort_keys=True)

    dt = time.time() - t0
    print(
        f"\nDONE pages={done['pages']} errors={done['errors']} known={len(seen)} "
        f"text={done['bytes'] / 1024 / 1024:.1f} MB in {dt:.0f}s -> {OUT_DIR}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
