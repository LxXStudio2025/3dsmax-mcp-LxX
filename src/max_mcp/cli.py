"""maxctl -- command line front end for the same bridge the MCP server uses.

Why this exists: debugging through an MCP client is slow (restart the client,
re-select the tool, wait for the model). Flue solves the same problem with a
CLI over a polling daemon, and the shape fits here exactly -- the bridge and the
tools already exist, only the front door was missing.

    maxctl status
    maxctl eval "1+1"
    maxctl eval - < script.ms
    maxctl query --pattern "SM_*" --limit 20
    maxctl get Box001
    maxctl set Box001 Box002 --prop wirecolor --value 255,0,0
    maxctl create box --name Test --param w=50 --param h=20
    maxctl shot --out shot.png
    maxctl repl

Exit code is 0 on success, 1 on a tool error, 2 on a usage error, so it can be
used from batch scripts without parsing output.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from . import tools

# ---------------------------------------------------------------------------
# output helpers
# ---------------------------------------------------------------------------


def _dump(value: Any) -> None:
    """Pretty print a result as JSON, falling back to repr for odd types."""
    try:
        print(json.dumps(value, indent=2, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        print(repr(value))


def _emit(args: argparse.Namespace, value: Any) -> None:
    if getattr(args, "json", False):
        _dump(value)
        return
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            # table-ish view for node rows
            cols = list(value[0].keys())
            widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in value)) for c in cols}
            print("  ".join(c.ljust(widths[c]) for c in cols))
            print("  ".join("-" * widths[c] for c in cols))
            for row in value:
                print("  ".join(str(row.get(c, "")).ljust(widths[c]) for c in cols))
        else:
            print(json.dumps(value, ensure_ascii=False, default=str))
        return
    if isinstance(value, dict):
        width = max((len(k) for k in value), default=0)
        for k, v in value.items():
            if isinstance(v, (list, dict)):
                v = json.dumps(v, ensure_ascii=False, default=str)
            print(f"{k.ljust(width)}  {v}")
        return
    print(value)


def _pairs(items: list[str] | None) -> dict:
    out = {}
    for item in items or []:
        if "=" not in item:
            raise SystemExit(f"--param needs k=v, got {item!r}")
        k, v = item.split("=", 1)
        try:
            out[k] = float(v) if "." in v else int(v)
        except ValueError:
            out[k] = v
    return out


def _parse_value(text: str) -> Any:
    """--value accepts 255,0,0 | 1.5 | true | hello | '{"json":1}'."""
    raw = text.strip()
    if raw.startswith(("{", "[")):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    if "," in raw:
        parts = [p.strip() for p in raw.split(",")]
        if all(p.replace(".", "", 1).replace("-", "", 1).isdigit() for p in parts):
            return [float(p) for p in parts]
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    info = tools.max_status()
    if getattr(args, "json", False):
        _dump(info)
    else:
        _dump(info)
        stats = tools.BRIDGE.stats()
        print()
        print(f"bridge    {stats['endpoint']}  online={stats['online']}  "
              f"served={stats['tasks_served']}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    code = sys.stdin.read() if args.code == "-" else args.code
    if not code.strip():
        print("empty script", file=sys.stderr)
        return 2
    _emit(args, tools.max_eval(code, timeout_seconds=args.timeout))
    return 0


def cmd_py(args: argparse.Namespace) -> int:
    code = sys.stdin.read() if args.code == "-" else args.code
    if not code.strip():
        print("empty script", file=sys.stderr)
        return 2
    _emit(args, tools.max_python(code, timeout_seconds=args.timeout))
    return 0


def cmd_doc(args: argparse.Namespace) -> int:
    res = tools.max_doc(args.keyword, limit=args.limit)
    if getattr(args, "json", False):
        _dump(res)
        return 0
    print(f"{res['match_count']} page(s) mention '{args.keyword}' "
          f"(searched {res['pages_searched']})")
    for hit in res["results"]:
        print()
        print(f"--- {hit['title']}  ({hit['hits_in_page']} hits) ---")
        print(f"    {hit['file']}")
        print(f"    {hit['snippet'][:400]}")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    res = tools.max_query_objects(
        name_pattern=args.pattern,
        class_name=args.cls or "",
        layer=args.layer or "",
        limit=args.limit,
        offset=args.offset,
    )
    if getattr(args, "json", False):
        _dump(res)
    else:
        print(f"{res['count']} row(s)  truncated={res['truncated']}  offset={res['offset']}")
        _emit(args, res["items"])
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    res = tools.max_get_object(args.name)
    if not res.get("found"):
        print(f"not found: {args.name}", file=sys.stderr)
        return 1
    _emit(args, res)
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    res = tools.max_set_property(args.names, args.prop, _parse_value(args.value))
    _emit(args, res)
    return 0 if not res.get("failed") else 1


def cmd_create(args: argparse.Namespace) -> int:
    pos = [float(x) for x in args.pos.split(",")] if args.pos else None
    res = tools.max_create_object(args.kind, args.name or "", pos, _pairs(args.param))
    _emit(args, res)
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    res = tools.max_delete_objects(args.names)
    _emit(args, res)
    return 0 if not res.get("missing") else 1


def cmd_summary(args: argparse.Namespace) -> int:
    _emit(args, tools.max_scene_summary(args.top))
    return 0


def cmd_shot(args: argparse.Namespace) -> int:
    path = tools.capture_to_file(
        render=args.render, width=args.width, height=args.height, out_path=args.out
    )
    print(path)
    return 0


def cmd_file(args: argparse.Namespace) -> int:
    _emit(args, tools.max_file_io(args.action, args.path, args.selected))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    # stats() reads the last poll time, which stays 0 until the HTTP endpoint is
    # open, so start it first and give Max a couple of ticks to show up.
    tools.BRIDGE.start()
    deadline = time.time() + 2.0
    while time.time() < deadline and not tools.BRIDGE.online:
        time.sleep(0.1)

    stats = tools.BRIDGE.stats()
    age = stats["last_poll_age_s"]
    age_text = "never" if age is None else f"{age}s ago"
    print(f"endpoint       {stats['endpoint']}")
    print(f"online         {stats['online']}")
    print(f"last poll      {age_text}")
    print(f"tasks served   {stats['tasks_served']}")
    if not stats["online"]:
        print()
        print("3ds Max is not polling. Checklist:")
        print("  1. Is 3ds Max 2016 running?")
        print("  2. Is max_mcp_bridge.ms in the startup folder, or did you run it once?")
        print("  3. MAXScript Listener (F11) should show: [max-mcp] ... polling ...")
        print("  4. Boot log tells you whether the file ever executed:")
        print(r"     %TEMP%\max-mcp-boot.log")
        return 1
    try:
        info = tools.max_status()
        print()
        print(f"Max            {info['max_version']} (build {info['max_build']})")
        print(f"scene          {info['scene_name'] or '(unsaved)'}")
        print(f"units          {info['unit_type']}")
        print(f"objects        {info['objects']}")
        return 0
    except Exception as exc:
        print(f"\nstatus call failed: {exc}", file=sys.stderr)
        return 1


def cmd_repl(args: argparse.Namespace) -> int:
    """Interactive MAXScript execution -- the fast debugging loop."""
    print("maxctl repl -- type MAXScript, empty line executes, :q quits")
    print("                :s = status, :q = quit, :m = multi-line mode")
    buf: list[str] = []
    multi = False
    while True:
        prompt = "... " if (buf or multi) else "mx> "
        try:
            line = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        stripped = line.strip()
        if not buf and not multi:
            if stripped in (":q", ":quit", "exit", "quit"):
                return 0
            if stripped == ":s":
                _dump(tools.max_status())
                continue
            if stripped == ":m":
                multi = True
                continue
        if multi:
            # in multi-line mode an empty line submits the whole buffer
            if stripped == "":
                multi = False
                code = "\n".join(buf)
                buf = []
                if code.strip():
                    try:
                        _dump(tools.max_eval(code, timeout_seconds=args.timeout))
                    except Exception as exc:
                        print(f"!! {exc}", file=sys.stderr)
                continue
            buf.append(line)
            continue
        if stripped == "":
            if buf:
                code = "\n".join(buf)
                buf = []
                try:
                    _dump(tools.max_eval(code, timeout_seconds=args.timeout))
                except Exception as exc:
                    print(f"!! {exc}", file=sys.stderr)
            continue
        buf.append(line)


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="maxctl",
        description="Command line control for a running 3ds Max 2016 (same bridge as the MCP server).",
    )
    p.add_argument("--port", type=int, default=None, help="bridge port (default 47100 or $MAXMCP_PORT)")
    p.add_argument("--json", action="store_true", help="raw JSON output")
    p.add_argument("--timeout", type=float, default=30.0, help="seconds to wait for Max")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("status", help="bridge + scene status")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("doctor", help="diagnose a missing connection")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("eval", help="run MAXScript ('-' reads stdin)")
    sp.add_argument("code")
    sp.set_defaults(func=cmd_eval)

    sp = sub.add_parser("py", help="run Python inside Max ('-' reads stdin)")
    sp.add_argument("code")
    sp.set_defaults(func=cmd_py)

    sp = sub.add_parser("doc", help="search the local MAXScript Reference")
    sp.add_argument("keyword")
    sp.add_argument("--limit", type=int, default=5)
    sp.set_defaults(func=cmd_doc)

    sp = sub.add_parser("query", help="list nodes")
    sp.add_argument("--pattern", default="*")
    sp.add_argument("--class", dest="cls", default="")
    sp.add_argument("--layer", default="")
    sp.add_argument("--limit", type=int, default=100)
    sp.add_argument("--offset", type=int, default=0)
    sp.set_defaults(func=cmd_query)

    sp = sub.add_parser("get", help="full detail for one node")
    sp.add_argument("name")
    sp.set_defaults(func=cmd_get)

    sp = sub.add_parser("set", help="set one property on many nodes")
    sp.add_argument("names", nargs="+")
    sp.add_argument("--prop", required=True)
    sp.add_argument("--value", required=True)
    sp.set_defaults(func=cmd_set)

    sp = sub.add_parser("create", help="create a primitive or import geometry")
    sp.add_argument("kind")
    sp.add_argument("--name", default="")
    sp.add_argument("--pos", default="")
    sp.add_argument("--param", action="append", metavar="k=v")
    sp.set_defaults(func=cmd_create)

    sp = sub.add_parser("delete", help="delete nodes by name")
    sp.add_argument("names", nargs="+")
    sp.set_defaults(func=cmd_delete)

    sp = sub.add_parser("summary", help="scene histogram / missing maps")
    sp.add_argument("--top", type=int, default=12)
    sp.set_defaults(func=cmd_summary)

    sp = sub.add_parser("shot", help="viewport grab or quick render")
    sp.add_argument("--render", action="store_true")
    sp.add_argument("--width", type=int, default=1280)
    sp.add_argument("--height", type=int, default=720)
    sp.add_argument("--out", default="")
    sp.set_defaults(func=cmd_shot)

    sp = sub.add_parser("file", help="import / export / merge")
    sp.add_argument("action", choices=["import", "export", "merge"])
    sp.add_argument("path")
    sp.add_argument("--selected", action="store_true")
    sp.set_defaults(func=cmd_file)

    sp = sub.add_parser("repl", help="interactive MAXScript")
    sp.set_defaults(func=cmd_repl)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.port:
        tools.BRIDGE.port = args.port
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"!! {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
