"""Install / uninstall the bridge into any installed 3ds Max, discovered from the registry.

Everything here is learned from DCC-Python's setup/base.py + setup/max.py, but
rewritten for this project's constraints (any Max from 2016 up, both ENU and CHS
UI languages, and a launcher that will not double-load):

  * version + install path come from HKLM\\SOFTWARE\\Autodesk\\3dsMax\\<ver>\\Installdir,
    never from a hard-coded path -- the user's 2016 lives in C:\\3d16, not Program Files
  * the UI language folder is ENU or CHS; guessing ENU silently breaks Chinese installs
  * the real script lives in <scripts>/maxmcp/, only a 2-line launcher goes into
    <scripts>/startup/. Putting the full script in startup means every copy found
    there runs, so an older copy in the *other* startup directory silently wins --
    which is exactly the bug we hit during bring-up
  * setup is idempotent and unsetup is real, so this is a tool rather than a copy-paste

Usage:
    python tools/install.py --list
    python tools/install.py --install           # all discovered versions
    python tools/install.py --install --version 2016 --lang zh_CN
    python tools/install.py --uninstall --version 2025
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

try:
    import winreg
except ImportError:  # pragma: no cover - non-Windows
    print("this installer only runs on Windows", file=sys.stderr)
    raise SystemExit(1)

REG_BASE = r"SOFTWARE\Autodesk\3dsMax"
BRIDGE_KEY = "maxmcp"          # subfolder under scripts/
LAUNCHER_NAME = "start_maxmcp.ms"
MAIN_NAME = "max_mcp_bridge.ms"

LANG_DIRS = {"en": "ENU", "zh_CN": "CHS"}


def _read(reg_path: str, value: str) -> str | None:
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, reg_path) as key:
                val, _ = winreg.QueryValueEx(key, value)
                return str(val)
        except OSError:
            continue
    return None


def _subkeys(reg_path: str) -> list[str]:
    out = []
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, reg_path) as key:
                i = 0
                while True:
                    try:
                        out.append(winreg.EnumKey(key, i))
                    except OSError:
                        break
                    i += 1
        except OSError:
            continue
    return out


def _year_of(text: str) -> str:
    """Pull a 20xx release year out of a key name or a path."""
    for tok in text.replace("\\", " ").replace("/", " ").replace("_", " ").split():
        if tok.isdigit() and len(tok) == 4 and tok.startswith("20"):
            return tok
    m = re.search(r"(20\d\d)", text)
    return m.group(1) if m else ""


def _from_filesystem() -> dict[str, str]:
    """Fallback for portable installs that never touched the registry.

    Needed in practice: this machine's 2016 is registered (C://3d16), but the
    2025 build is a straight extract with no registry trace at all, so a
    registry-only discovery silently misses it.
    """
    import glob

    found: dict[str, str] = {}
    drives = [f"{d}:\\" for d in "CDEFGH" if os.path.isdir(f"{d}:\\")]
    for root in drives:
        for pattern in (
            os.path.join(root, "Program Files", "Autodesk", "3ds Max *", "3dsmax.exe"),
            os.path.join(root, "Program Files (x86)", "Autodesk", "3ds Max *", "3dsmax.exe"),
            os.path.join(root, "Autodesk", "3ds Max *", "3dsmax.exe"),
            os.path.join(root, "3d*", "3ds Max *", "3dsmax.exe"),
            os.path.join(root, "3ds Max *", "3dsmax.exe"),
        ):
            for exe in glob.glob(pattern):
                install = os.path.dirname(exe)
                year = _year_of(os.path.basename(install)) or _year_of(install)
                if year:
                    found.setdefault(year, install)
    return found


def discover() -> list[dict]:
    """Every 3ds Max we can find: version year -> install dir.

    Three sources, in order of trust: the Autodesk key, the uninstall entry,
    then a filesystem sweep for portable builds.
    """
    found: dict[str, str] = {}

    # 1. HKLM/HKCU\SOFTWARE\Autodesk\3dsMax\<ver>\Installdir
    for sk in _subkeys(REG_BASE):
        install = _read(rf"{REG_BASE}\{sk}", "Installdir")
        if not install:
            continue
        year = _year_of(sk) or _year_of(install)
        if year:
            found.setdefault(year, install)

    # 2. uninstall entries
    for hive in ("SOFTWARE", r"SOFTWARE\WOW6432Node"):
        uninst = rf"{hive}\Microsoft\Windows\CurrentVersion\Uninstall"
        for sk in _subkeys(uninst):
            name = _read(rf"{uninst}\{sk}", "DisplayName") or ""
            if "3ds Max" not in name or "Interoperability" in name or "SDK" in name:
                continue
            loc = _read(rf"{uninst}\{sk}", "InstallLocation")
            if not loc:
                continue
            year = _year_of(name) or _year_of(loc)
            if year:
                found.setdefault(year, loc)

    # 3. filesystem sweep (portable installs)
    for year, install in _from_filesystem().items():
        found.setdefault(year, install)

    return [{"version": v, "install_dir": found[v].rstrip("\\")} for v in sorted(found)]


# The stub writes a marker before doing anything else. Without it there is no
# way to tell "startup never ran this file" from "fileIn inside it failed" --
# both look identical from outside (silent, no bridge).
LAUNCHER_TEMPLATE = """\
-- max-mcp launcher (generated by tools/install.py)
-- Remove with: python tools/install.py --uninstall --version {year}
-- Only this stub lives in startup/; the real script lives in scripts/maxmcp/.
--
-- Re-entry guard: this stub may legitimately exist in BOTH the install-dir
-- startup/ and the per-user AppData startup/. Whether 3ds Max reads one or both
-- differs by release (2016 reads only the install-dir one), so rather than guess
-- we install into both and let this flag make the second copy a no-op. Without
-- it you get two WinForms timers polling the same port.
--
-- The marker file is written before anything else because "startup never ran
-- this file" and "it ran but fileIn failed" look identical from outside.
if __MAXMCP_LAUNCHED == undefined then
(
    global __MAXMCP_LAUNCHED = true
    (
        local __mark = (sysInfo.tempdir) + "maxmcp-launcher-{year}.txt"
        local __h = undefined
        try ( __h = createFile __mark ) catch()
        if __h != undefined do
        (
            try ( format "launcher ran (first)" to:__h ) catch()
            try ( close __h ) catch()
        )
        try ( fileIn (@"{main_script}") )
        catch ( format "[max-mcp] launcher fileIn FAILED: %" (getCurrentException()) )
    )
)
else
(
    format "[max-mcp] launcher already ran this session, skipping second copy"
)
"""


def install_one(rec: dict, langs: list[str], quiet: bool = False, system: bool = True, user: bool = False) -> int:
    year = rec["version"]
    src_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "maxscript")
    main_src = os.path.join(src_dir, MAIN_NAME)
    if not os.path.isfile(main_src):
        print(f"  !! missing {main_src}", file=sys.stderr)
        return 1

    targets = []
    # Install-dir scripts/ is the one Max 2016 actually loads (it is where the
    # user's own LxXStudio_Menu.ms lives). The AppData tree exists but is not
    # read by 2016 -- writing only there produces a launcher that never runs.
    inst_scripts = os.path.join(rec["install_dir"], "scripts")
    if os.path.isdir(inst_scripts):
        targets.append(inst_scripts)
    home_base = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Autodesk", "3dsMax")
    if user and os.path.isdir(home_base):
        for entry in os.listdir(home_base):
            if not entry.startswith(f"{year} "):
                continue
            for lang in langs:
                scripts = os.path.join(home_base, entry, LANG_DIRS[lang], "scripts")
                if os.path.isdir(scripts):
                    targets.append(scripts)
    # A bare bridge copy in any startup/ would run alongside our launcher and
    # start a second timer. Sweep both locations.
    for _scripts in [os.path.join(rec["install_dir"], "scripts")] + [
        os.path.join(os.path.expanduser("~"), "AppData", "Local", "Autodesk", "3dsMax", e, LANG_DIRS[langs[0]], "scripts")
        for e in ([x for x in os.listdir(os.path.join(os.path.expanduser("~"), "AppData", "Local", "Autodesk", "3dsMax")) if x.startswith(f"{year} ")]
                  if os.path.isdir(os.path.join(os.path.expanduser("~"), "AppData", "Local", "Autodesk", "3dsMax")) else [])
    ]:
        stale_sys = os.path.join(_scripts, "startup", MAIN_NAME)
        if os.path.isfile(stale_sys):
            try:
                os.remove(stale_sys)
                if not quiet:
                    print(f"  removed stale copy: {stale_sys}")
            except OSError as exc:
                print(f"  !! cannot remove {stale_sys}: {exc}", file=sys.stderr)

    if not targets:
        print(f"  !! {year}: no scripts directory found under {rec['install_dir']} or %LOCALAPPDATA%",
              file=sys.stderr)
        return 1

    for scripts in targets:
        payload_dir = os.path.join(scripts, BRIDGE_KEY)
        startup_dir = os.path.join(scripts, "startup")
        os.makedirs(payload_dir, exist_ok=True)
        os.makedirs(startup_dir, exist_ok=True)

        main_dst = os.path.join(payload_dir, MAIN_NAME)
        shutil.copy2(main_src, main_dst)

        launcher_dst = os.path.join(startup_dir, LAUNCHER_NAME)
        with open(launcher_dst, "w", encoding="utf-8") as fh:
            fh.write(LAUNCHER_TEMPLATE.format(year=year, main_script=main_dst))

        # A bare bridge copy in startup/ would run in addition to the launcher and
        # start a second timer. Remove any stale one from earlier installs.
        stale = os.path.join(startup_dir, MAIN_NAME)
        removed = ""
        if os.path.isfile(stale):
            try:
                os.remove(stale)
                removed = "  (removed stale startup copy)"
            except OSError:
                removed = "  (WARNING: stale copy still present)"
        if not quiet:
            print(f"  {scripts}")
            print(f"    main     -> {main_dst}")
            print(f"    launcher -> {launcher_dst}{removed}")
    return 0


def uninstall_one(rec: dict, langs: list[str], system: bool = True) -> int:
    year = rec["version"]
    n = 0
    home_base = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Autodesk", "3dsMax")
    roots = []
    if os.path.isdir(home_base):
        for entry in os.listdir(home_base):
            if entry.startswith(f"{year} "):
                for lang in langs:
                    s = os.path.join(home_base, entry, LANG_DIRS[lang], "scripts")
                    if os.path.isdir(s):
                        roots.append(s)
    if system:
        roots.append(os.path.join(rec["install_dir"], "scripts"))

    for scripts in roots:
        for path in (
            os.path.join(scripts, "startup", LAUNCHER_NAME),
            os.path.join(scripts, "startup", MAIN_NAME),
            os.path.join(scripts, BRIDGE_KEY, MAIN_NAME),
        ):
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    print(f"  removed {path}")
                    n += 1
                except OSError as exc:
                    print(f"  !! cannot remove {path}: {exc}", file=sys.stderr)
        d = os.path.join(scripts, BRIDGE_KEY)
        if os.path.isdir(d) and not os.listdir(d):
            try:
                os.rmdir(d)
            except OSError:
                pass
    return 0 if n else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--install", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--version", default="")
    ap.add_argument("--lang", default="en",
                    help="UI language folder(s): en (ENU) or zh_CN (CHS). "
                         "Install ONLY the one your Max actually uses -- two launchers means two timers")
    ap.add_argument("--user", action="store_true",
                    help="also install into %%LOCALAPPDATA%%\...\scripts (2016 does NOT read it; 2020+ might)")
    args = ap.parse_args()

    found = discover()
    if not found:
        print("no 3ds Max installation found in the registry", file=sys.stderr)
        return 1

    if args.list or not (args.install or args.uninstall):
        print(f"{'year':<6} install dir")
        for rec in found:
            print(f"{rec['version']:<6} {rec['install_dir']}")
        if not args.list:
            print("\nuse --install / --uninstall")
        return 0

    langs = [x.strip() for x in args.lang.split(",") if x.strip()]
    for bad in langs:
        if bad not in LANG_DIRS:
            print(f"unknown language {bad!r}, pick from {list(LANG_DIRS)}", file=sys.stderr)
            return 2

    targets = [r for r in found if not args.version or r["version"] == args.version]
    if not targets:
        print(f"version {args.version} not installed", file=sys.stderr)
        return 1

    rc = 0
    for rec in targets:
        print(f"[{rec['version']}] {rec['install_dir']}")
        if args.install:
            rc |= install_one(rec, langs, user=args.user)
        if args.uninstall:
            rc |= uninstall_one(rec, langs)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
