"""Diff two api-snapshot-<year>.txt files produced by `showClass "*.*"`.

Runtime snapshots beat comparing documentation: docs describe intent and lag by
a release, the running process describes what actually exists. This is the
authoritative answer to "does 2016 have this API?".

Format of a snapshot line:
    Box(Box) : GeometryClass {10,0}            <- class header
      .height(Height) : float                  <- member
The parenthesised part is a localized display name and is stripped before
comparison, otherwise 2016's Chinese names would make everything look different.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

# Loose on purpose: class headers carry localized display names with NESTED
# parentheses, e.g. "Standardmaterial(Standard (Legacy)) : material {2,0}".
# A strict optional-group regex stops at the first ')' and loses the class.
HEADER_RE = re.compile(r"^([A-Za-z_][\w]*).*?:\s*([A-Za-z_][\w]*)")
# Member names are compared case-insensitively downstream: 3ds Max renamed
# mapcoords -> mapCoords between 2016 and 2025 and that is not a real API change.
MEMBER_RE = re.compile(r"\.([A-Za-z_][\w]*)\s*(?:\([^)]*\))?\s*:\s*(\S+)")


def load(path: str) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Return ({class: {members}}, {class: superclass})."""
    for enc in ("gbk", "utf-8", "latin-1"):
        try:
            with open(path, encoding=enc) as fh:
                lines = fh.read().splitlines()
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        raise SystemExit(f"cannot decode {path}")

    classes: dict[str, set[str]] = {}
    supers: dict[str, str] = {}
    current = ""
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("."):
            m = MEMBER_RE.search(line)
            if m and current:
                classes[current].add(m.group(1))
            continue
        m = HEADER_RE.match(line.strip())
        if m:
            current = m.group(1)
            classes.setdefault(current, set())
            if m.group(2):
                supers[current] = m.group(2)
    return classes, supers


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True, help="older snapshot (e.g. api-snapshot-2016.txt)")
    ap.add_argument("--b", required=True, help="newer snapshot (e.g. api-snapshot-2025.txt)")
    ap.add_argument("--out", default="", help="markdown report path")
    ap.add_argument("--member-limit", type=int, default=400)
    args = ap.parse_args()

    ca, sa = load(args.a)
    cb, sb = load(args.b)

    def fold(d):
        return {k: {m.lower(): m for m in v} for k, v in d.items()}

    fa, fb = fold(ca), fold(cb)
    only_a = sorted(set(fa) - set(fb))
    only_b = sorted(set(fb) - set(fa))
    common = sorted(set(fa) & set(fb))

    member_diff: dict[str, dict[str, list[str]]] = {}
    for cls in common:
        ma, mb = fa[cls], fb[cls]
        gone = sorted(ma[k] for k in set(ma) - set(mb))
        new = sorted(mb[k] for k in set(mb) - set(ma))
        if gone or new:
            member_diff[ca.get(cls) and cls or cls] = {"only_in_a": gone, "only_in_b": new}

    tot_a = sum(len(v) for v in ca.values())
    tot_b = sum(len(v) for v in cb.values())

    lines = [
        f"# API diff: {os.path.basename(args.a)} vs {os.path.basename(args.b)}",
        "",
        "Generated from `showClass \"*.*\"` run inside each Max -- runtime truth, not docs.",
        "",
        "## Totals",
        "",
        "| | classes | members |",
        "|---|---|---|",
        f"| A ({os.path.basename(args.a)}) | {len(ca)} | {tot_a} |",
        f"| B ({os.path.basename(args.b)}) | {len(cb)} | {tot_b} |",
        f"| common | {len(common)} | |",
        f"| **only in A** | **{len(only_a)}** | |",
        f"| **only in B** | **{len(only_b)}** | |",
        f"| classes with member drift | {len(member_diff)} | |",
        "",
        f"## Classes only in A ({len(only_a)}) — gone in B",
        "",
    ]
    for name in only_a[: args.member_limit]:
        sup = sa.get(name, "?")
        lines.append(f"- `{name}` : {sup}")
    if len(only_a) > args.member_limit:
        lines.append(f"- ... and {len(only_a) - args.member_limit} more")

    lines += ["", f"## Classes only in B ({len(only_b)}) — new since A", ""]
    for name in only_b[: args.member_limit]:
        sup = sb.get(name, "?")
        lines.append(f"- `{name}` : {sup}")
    if len(only_b) > args.member_limit:
        lines.append(f"- ... and {len(only_b) - args.member_limit} more")

    lines += ["", f"## Member drift in shared classes ({len(member_diff)})", ""]
    shown = 0
    for cls in sorted(member_diff):
        if shown >= args.member_limit:
            lines.append(f"- ... and {len(member_diff) - shown} more classes")
            break
        info = member_diff[cls]
        parts = []
        if info["only_in_a"]:
            parts.append("only in A: " + ", ".join(info["only_in_a"][:25]))
        if info["only_in_b"]:
            parts.append("only in B: " + ", ".join(info["only_in_b"][:25]))
        lines.append(f"- `{cls}` — " + " | ".join(parts))
        shown += 1

    report = "\n".join(lines) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)
        print(f"wrote {args.out}  ({len(report)} chars)")
    print(f"classes A={len(ca)} B={len(cb)} common={len(common)} onlyA={len(only_a)} onlyB={len(only_b)}")
    print(f"members A={tot_a} B={tot_b} drift_classes={len(member_diff)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
