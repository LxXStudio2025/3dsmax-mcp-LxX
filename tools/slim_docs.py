"""Condense the crawled MAXScript Reference into a lean search corpus.

The raw crawl is 2684 pages / 16 MB, most of it prose examples, tutorials and
narrative. For an AI-facing lookup you only need: what the thing is called, its
signature, its arguments, and one sentence of meaning.

Rules (deliberately conservative -- dropping the wrong line costs more than
keeping a verbose one):
  * fenced code blocks longer than KEEP_CODE_LINES are cut to their first lines
  * paragraphs that are pure narrative and longer than MAX_PARA chars are cut
    to their first sentence
  * sections whose heading matches SKIP_HEADINGS are dropped whole
  * signature-looking lines (<name> ... , name: type) are always kept

Usage: python slim_docs.py [--src docs/maxscript-2025] [--dst docs/maxscript-2025-slim]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

SKIP_HEADINGS = (
    "example",
    "examples",
    "tutorial",
    "tutorials",
    "how to",
    "how-to",
    "frequently asked",
    "faq",
    "what is new",
    "what's new",
    "sample",
    "demonstrat",
    "walkthrough",
    "step by step",
)
KEEP_CODE_LINES = 3
MAX_PARA = 120
MAX_PAGE_CHARS = 420  # hard cap per page: signature + one short paragraph
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def condense(text: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    skip_until_level = 0  # >0 means we are inside a skipped section
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # ---- headings control section skipping
        h = HEADING_RE.match(line)
        if h:
            level = len(h.group(1))
            title = h.group(2).strip().lower()
            if skip_until_level and level <= skip_until_level:
                skip_until_level = 0
            if not skip_until_level and any(k in title for k in SKIP_HEADINGS):
                skip_until_level = level
                i += 1
                continue
            if skip_until_level:
                i += 1
                continue
            out.append(line)
            i += 1
            continue

        if skip_until_level:
            i += 1
            continue

        # ---- fenced code blocks
        if line.strip().startswith("```"):
            fence = line.strip()
            block = [line]
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            if i < n:
                block.append(lines[i])
                i += 1
            if len(block) <= KEEP_CODE_LINES + 2:
                out.extend(block)
            else:
                out.extend(block[: KEEP_CODE_LINES + 1])
                out.append("...")
                out.append(fence)
            continue

        # ---- signature-ish lines always survive
        stripped = line.strip()
        if stripped and re.match(r"^[<A-Za-z_][\w.<>\s]*\s*<", stripped):
            out.append(line)
            i += 1
            continue

        # ---- long narrative paragraph: keep first sentence only
        if len(stripped) > MAX_PARA:
            first = re.split(r"(?<=[.!?])\s", stripped, maxsplit=1)[0]
            if len(first) > MAX_PARA:
                first = first[:MAX_PARA].rsplit(" ", 1)[0] + "..."
            out.append(first)
            i += 1
            continue

        out.append(line)
        i += 1

    # collapse blank runs
    cleaned: list[str] = []
    for ln in out:
        if ln.strip() == "" and cleaned and cleaned[-1].strip() == "":
            continue
        cleaned.append(ln)

    # hard cap: the header line and any signature line come first, then fill
    head = [ln for ln in cleaned if ln.startswith("# ")]
    sigs = [ln for ln in cleaned if re.match(r"^\s*[<A-Za-z_][\w.<>\s]*\s*<", ln)]
    body: list[str] = []
    used = sum(len(x) + 1 for x in head + sigs)
    for ln in cleaned:
        if ln in head or ln in sigs:
            continue
        if used + len(ln) + 1 > MAX_PAGE_CHARS:
            break
        body.append(ln)
        used += len(ln) + 1
    result = "\n".join(head + sigs + body)
    return re.sub(r"\n{3,}", "\n\n", result).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--src", default=os.path.join(here, "docs", "maxscript-2025"))
    ap.add_argument("--dst", default=os.path.join(here, "docs", "maxscript-2025-slim"))
    args = ap.parse_args()

    if not os.path.isdir(args.src):
        print(f"missing {args.src}", file=sys.stderr)
        return 1
    os.makedirs(args.dst, exist_ok=True)

    before = after = pages = 0
    index: dict[str, dict] = {}
    for name in sorted(os.listdir(args.src)):
        if not name.endswith(".md"):
            continue
        with open(os.path.join(args.src, name), encoding="utf-8") as fh:
            raw = fh.read()
        slim = condense(raw)
        if len(slim) < 80:  # nothing but a title -- drop it
            continue
        with open(os.path.join(args.dst, name), "w", encoding="utf-8") as fh:
            fh.write(slim + "\n")
        title = ""
        for ln in slim.splitlines():
            if ln.startswith("# "):
                title = ln[2:].strip()
                break
        index[name] = {"title": title or name, "chars": len(slim)}
        before += len(raw)
        after += len(slim)
        pages += 1

    with open(os.path.join(args.dst, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=1, ensure_ascii=False, sort_keys=True)

    ratio = (after / before * 100) if before else 0
    print(
        f"pages={pages}  {before / 1024 / 1024:.1f} MB -> {after / 1024 / 1024:.1f} MB  ({ratio:.0f}%)"
    )
    print(f"out: {args.dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
