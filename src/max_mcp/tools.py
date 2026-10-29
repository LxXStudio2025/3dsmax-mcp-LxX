"""MCP tools exposed to the AI client.

Every tool renders its parameters into MAXScript source on the Python side and
hands the snippet to the bridge; the Max side therefore only needs a JSON
*encoder*. Keeping parsing out of MAXScript is the biggest robustness win here
-- Max 2016 ships no JSON parser and a hand-rolled one is where naive bridges
break.

Records travel as flat key/value arrays (`#("k", v, "k", v)`) and are zipped
into dicts by `flat_to_dict`, because MAXScript has no dictionary literal.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from typing import Any

from .bridge import Bridge, MaxError

BRIDGE = Bridge(port=int(os.environ.get("MAXMCP_PORT", "47100")))
SHOT_DIR = os.path.join(tempfile.gettempdir(), "max-mcp")

# Extensions that `save <bitmap>` can actually write. Used to reject a typo'd
# name before the image bytes overwrite whatever was there.
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".tif", ".tiff", ".exr"}

_SWEPT = False


def _sweep_temp(max_age_hours: float = 6.0) -> None:
    """Remove py_*.py / py_*.json / shot_*.png a previous session could not delete.

    A timed-out max_python gives up *before* Max writes the result file, so the
    call cannot delete it -- and its random name means nothing will ever look at
    that path again. Screenshots written by an older build were never removed at
    all. Collect them once per process, by age, on first use.
    """
    global _SWEPT
    if _SWEPT:
        return
    _SWEPT = True
    junk = (".py", ".json", ".png")
    try:
        cutoff = time.time() - max_age_hours * 3600.0
        for name in os.listdir(SHOT_DIR):
            if not (name.startswith(("py_", "shot_")) and name.endswith(junk)):
                continue
            fp = os.path.join(SHOT_DIR, name)
            try:
                if os.path.getmtime(fp) < cutoff:
                    os.remove(fp)
            except OSError:
                pass
    except OSError:
        pass


# ---------------------------------------------------------------------------
# MAXScript literal helpers
# ---------------------------------------------------------------------------


def mxs_str(value: str) -> str:
    """Render a Python string as a MAXScript string literal."""
    s = str(value)
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    s = s.replace("\n", "\\n")
    s = s.replace("\r", "\\r")
    s = s.replace("\t", "\\t")
    return f'"{s}"'


def mxs_lit(value: Any) -> str:
    """Render a Python value as a MAXScript literal."""
    if value is None:
        return "undefined"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return mxs_str(value)
    if isinstance(value, (list, tuple)):
        numeric = all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
        )
        if numeric and len(value) == 3:
            return "[{}, {}, {}]".format(*[repr(float(v)) for v in value])
        items = []
        for v in value:
            if isinstance(v, str):
                items.append(mxs_str(v))
            elif isinstance(v, bool):
                items.append("true" if v else "false")
            elif v is None:
                items.append("undefined")
            else:
                items.append(mxs_lit(v))
        return "#(" + ", ".join(items) + ")"
    return mxs_str(str(value))


def mxs_list(values: list[Any]) -> str:
    return "#(" + ", ".join(mxs_str(v) for v in values) + ")"


def lookup_block(var: str, name_expr: str, tag: str = "a") -> str:
    """MAXScript that resolves exactly one node by name, refusing ambiguity.

    `getNodeByName` is NOT safe for this job, which cost correctness until
    cl0nazepamm/3dsmax-mcp's helpers/maxscript.py spelled it out:

      - it defaults to `exact:false`, so "Box001" also matches "Box 001" and
        "Box_001";
      - with duplicate names it silently returns whichever node it meets first.

    Both bite hard in asset pipelines, where near-identical names are the norm
    and a silent wrong pick means editing the wrong asset. Scanning `objects`
    with `==` is exact by construction and avoids the optional arguments
    (`all:`, `ignoreCase:`) whose availability varies by Max version.
    """
    return (
        f"    local {var} = undefined\n"
        f"    local __hits{tag} = for __cand in objects where (__cand.name == {name_expr}) collect __cand\n"
        f'    if __hits{tag}.count > 1 do throw ("ambiguous name \'" + {name_expr} + "\': "'
        f' + (__hits{tag}.count as string) + " objects share it")\n'
        f"    if __hits{tag}.count == 1 do {var} = __hits{tag}[1]\n"
    )


def flat_to_dict(flat: Any) -> dict:
    """Max emits records as flat key/value arrays; pair them up here."""
    if not isinstance(flat, list):
        return {"value": flat}
    return {str(flat[i]): flat[i + 1] for i in range(0, len(flat) - 1, 2)}


def call(script: str, timeout: float = 30.0) -> Any:
    return BRIDGE.call(script, timeout=timeout)


# ---------------------------------------------------------------------------
# 1. status
# ---------------------------------------------------------------------------


def max_status() -> dict:
    """Check the bridge and report 3ds Max build, scene, units, timeline, renderer.

    Call this first in a session: it tells you whether Max is reachable, which
    scene is loaded, and what units the numbers in every other tool mean.
    """
    script = """(
    local __yv = (maxVersion())[1]
    -- maxVersion() reports e.g. 18000 for 3ds Max 2016; 2016 = 18000/1000 + 1998
    local __year = ((__yv / 1000) + 1998) as integer
    #(
        "bridge_version", (if __MAXMCP_VER == undefined then "?" else __MAXMCP_VER),
        "max_version", (__year as string),
        "max_build", ((maxVersion())[2] as string),
        "scene_name", maxFileName,
        "scene_path", maxFilePath,
        "dirty", (getSaveRequired()),
        "unit_type", (units.SystemType as string),
        "unit_scale", units.SystemScale,
        "frame_start", (animationRange.start as integer),
        "frame_end", (animationRange.end as integer),
        "fps", frameRate,
        "current_frame", (sliderTime as integer),
        "objects", objects.count,
        "geometry", geometry.count,
        "shapes", shapes.count,
        "lights", lights.count,
        "cameras", cameras.count,
        "helpers", helpers.count,
        "selection", selection.count,
        "layer_count", LayerManager.count,
        "renderer", (renderers.current as string)
    )
)"""
    return flat_to_dict(call(script, timeout=15))


# ---------------------------------------------------------------------------
# 2. raw eval
# ---------------------------------------------------------------------------


def max_eval(code: str, timeout_seconds: float = 30.0) -> Any:
    """Execute raw MAXScript in the live Max session and return the resulting value.

    This is the escape hatch for everything the structured tools do not cover.
    The code runs verbatim (no wrapper is added), so write a complete
    expression and wrap multi-statement code in `( ... )`. The value of the
    last expression is serialized back to JSON:

      Integer/Float -> number, String -> string, Point3 -> [x,y,z],
      Array -> array (first 2000 items), node/material/anything else -> its name.

    Nothing is auto-saved and no undo block is opened for you. Wrap writes in
    `undo "label" on ( ... )` when you want them reversible.

    Args:
        code: MAXScript source. Multi-statement code must be wrapped in ( ).
        timeout_seconds: how long to wait before giving up (Max may still be busy).
    """
    return call(code, timeout=timeout_seconds)


# ---------------------------------------------------------------------------
# 3. scene summary
# ---------------------------------------------------------------------------


def max_scene_summary(top_n: int = 12) -> dict:
    """Aggregate the whole scene: class histogram, missing bitmaps, layers.

    Use this to orient yourself before writing queries -- it answers "what kind
    of scene is this, and is anything broken" in one call.

    Args:
        top_n: how many entries to keep in the class histogram.
    """
    script = f"""(
    local __top = {int(top_n)}
    local __names = #()
    local __counts = #()

    for __o in objects do
    (
        local __c = ((classof __o) as string)
        local __i = findItem __names __c
        if __i == 0 then ( append __names __c; append __counts 1 )
        else ( __counts[__i] = __counts[__i] + 1 )
    )

    local __pairs = for __i = 1 to __names.count collect #(__names[__i], __counts[__i])
    fn __desc a b = ( b[2] - a[2] )
    try ( qsort __pairs __desc ) catch()

    local __flat = #()
    local __k = 0
    for __p in __pairs while __k < __top do ( append __flat __p[1]; append __flat __p[2]; __k += 1 )

    local __layers = #()
    try ( __layers = for __i = 0 to (LayerManager.count - 1) collect ((LayerManager.getLayer __i).name) ) catch()

    local __miss = #()
    try
    (
        local __seen = #()
        for __o in objects do
        (
            try
            (
                if __o.material != undefined do
                (
                    local __bt = getClassInstances Bitmaptexture target:__o.material
                    for __b in __bt do
                    (
                        local __fn = __b.filename
                        if __fn != undefined and __fn != "" do
                        (
                            if (findItem __seen __fn) == 0 do
                            (
                                append __seen __fn
                                if (doesFileExist __fn) == false do append __miss __fn
                            )
                        )
                    )
                )
            ) catch()
        )
    ) catch()

    local __bitmaps = #()
    try
    (
        local __bt2 = getClassInstances Bitmaptexture
        __bitmaps = for __b in __bt2 collect __b.filename
    ) catch()

    #(
        "object_count", objects.count,
        "by_class", __flat,
        "layers", __layers,
        "bitmap_count", __bitmaps.count,
        "missing_bitmaps", __miss,
        "group_count", (for __g in objects where (isGroupHead __g) collect 1).count
    )
)"""
    return flat_to_dict(call(script, timeout=60))


# ---------------------------------------------------------------------------
# 4. query objects
# ---------------------------------------------------------------------------


def max_query_objects(
    name_pattern: str = "*",
    class_name: str = "",
    layer: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List scene nodes matching a filter. Returns compact rows, not full dumps.

    Row fields: name, class, layer, material, verts, faces, pos, hidden, frozen, children.
    Scanning stops as soon as `limit` rows are collected, so `truncated: true`
    means "there are more matches", not an exact total.

    Args:
        name_pattern: MAXScript matchPattern syntax, e.g. "Box*", "SM_*_LOD0", "*".
        class_name: exact class filter such as "Box", "Editable_Poly", "VRayMesh". Empty = all.
        layer: exact layer name. Empty = all.
        limit: max rows to return (1-2000).
        offset: skip this many matches first, for paging.
    """
    limit = max(1, min(int(limit), 2000))
    offset = max(0, int(offset))
    script = f"""(
    local __pat  = {mxs_str(name_pattern)}
    local __cls  = {mxs_str(class_name)}
    local __lay  = {mxs_str(layer)}
    local __lim  = {limit}
    local __off  = {offset}
    local __seen = 0
    local __rows = #()

    for __o in objects do
    (
        if __rows.count >= __lim do exit

        local __ok = (matchPattern __o.name pattern:__pat)
        if __ok and __cls != "" do __ok = (((classof __o) as string) == __cls)
        if __ok and __lay != "" do __ok = (((__o.layer).name) == __lay)

        if __ok do
        (
            __seen += 1
            if __seen > __off do
            (
                local __mat = ""
                try ( if __o.material != undefined do __mat = (__o.material.name as string) ) catch()
                local __v = 0
                try ( __v = __o.mesh.numverts ) catch()
                local __f = 0
                try ( __f = __o.mesh.numfaces ) catch()
                append __rows #(
                    "name", __o.name,
                    "class", ((classof __o) as string),
                    "layer", ((__o.layer).name),
                    "material", __mat,
                    "verts", __v,
                    "faces", __f,
                    "pos", __o.pos,
                    "hidden", __o.isHidden,
                    "frozen", __o.isFrozen,
                    "children", __o.children.count
                )
            )
        )
    )

    local __out = for __r in __rows collect (
        local __d = #()
        for __i = 1 to __r.count by 2 do ( append __d __r[__i]; append __d __r[__i+1] )
        __d
    )

    #(
        "count", __out.count,
        "truncated", (__out.count >= __lim),
        "offset", __off,
        "items", __out
    )
)"""
    raw = flat_to_dict(call(script, timeout=90))
    items = raw.get("items") or []
    raw["items"] = [flat_to_dict(r) for r in items if isinstance(r, list)]
    return raw


# ---------------------------------------------------------------------------
# 5. get object
# ---------------------------------------------------------------------------


def max_get_object(name: str) -> dict:
    """Full detail for one node: transform, modifier stack, material, hierarchy, bbox.

    Cheaper and safer than a max_eval for "tell me everything about this object".

    Args:
        name: exact node name, e.g. "Box001".
    """
    script = f"""(
{lookup_block("__o", mxs_str(name), "g")}    if __o == undefined then ( #("found", false) )
    else
    (
        local __mods = #()
        try ( __mods = for __m in __o.modifiers collect ((classof __m) as string) ) catch()

        local __kids = #()
        try ( __kids = for __k in __o.children collect __k.name ) catch()

        local __matName = ""
        local __matClass = ""
        try ( if __o.material != undefined do
            ( __matName = (__o.material.name as string); __matClass = ((classof __o.material) as string) ) ) catch()

        local __maps = #()
        try ( if __o.material != undefined do
        (
            local __bt = getClassInstances Bitmaptexture target:__o.material
            __maps = for __b in __bt collect ((__b.filename as string))
        ) ) catch()

        local __v = 0
        try ( __v = __o.mesh.numverts ) catch()
        local __f = 0
        try ( __f = __o.mesh.numfaces ) catch()

        local __bmin = undefined
        local __bmax = undefined
        try ( __bmin = [__o.min.x, __o.min.y, __o.min.z]; __bmax = [__o.max.x, __o.max.y, __o.max.z] ) catch()

        local __par = ""
        try ( if __o.parent != undefined do __par = __o.parent.name ) catch()

        #(
            "found", true,
            "name", __o.name,
            "class", ((classof __o) as string),
            "superclass", ((superclassof __o) as string),
            "baseobject", ((classof __o.baseobject) as string),
            "layer", ((__o.layer).name),
            "parent", __par,
            "children", __kids,
            "child_count", __kids.count,
            "pos", __o.pos,
            "rotation", (__o.rotation as string),
            "scale", __o.scale,
            "pivot", __o.pivot,
            "modifiers", __mods,
            "material_name", __matName,
            "material_class", __matClass,
            "bitmaps", __maps,
            "verts", __v,
            "faces", __f,
            "hidden", __o.isHidden,
            "frozen", __o.isFrozen,
            "bbox_min", __bmin,
            "bbox_max", __bmax
        )
    )
)"""
    return flat_to_dict(call(script, timeout=30))


# ---------------------------------------------------------------------------
# 6. set property
# ---------------------------------------------------------------------------


def max_set_property(names: list[str], prop: str, value: Any) -> dict:
    """Set one property on many nodes inside a single undo block.

    Examples: prop="wirecolor" value=[255,0,0]; prop="name" value="SM_x";
    prop="pos" value=[0,0,100]; prop="visibility" value=False.

    Args:
        names: node names to touch.
        prop: property name as MAXScript sees it (getProperty/setProperty).
        value: number, string, bool, or 3-element list for Point3.
    """
    if not names:
        raise ValueError("names must not be empty")
    script = f"""(
    local __names = {mxs_list(names)}
    local __prop  = {mxs_str(prop)}
    local __val   = {mxs_lit(value)}
    local __ok    = 0
    local __fail  = #()

    undo "MCP: set property" on
    (
        for __nm in __names do
        (
{lookup_block("__o", "__nm", "s")}            if __o == undefined then ( append __fail (__nm + " <not found>") )
            else
            (
                try ( setProperty __o __prop __val; __ok += 1 )
                catch ( append __fail (__nm + ": " + (getCurrentException())) )
            )
        )
    )
    #("changed", __ok, "failed", __fail, "total", __names.count)
)"""
    return flat_to_dict(call(script, timeout=180))


# ---------------------------------------------------------------------------
# 7. create object
# ---------------------------------------------------------------------------


_PRIMITIVES = {
    "box": "Box width:{w} length:{l} height:{h} pos:{pos}",
    "sphere": "Sphere radius:{r} pos:{pos}",
    "cylinder": "Cylinder radius:{r} height:{h} pos:{pos}",
    "plane": "Plane width:{w} length:{l} pos:{pos}",
    "teapot": "Teapot radius:{r} pos:{pos}",
    "cone": "Cone radius1:{r} radius2:0 height:{h} pos:{pos}",
    "torus": "Torus radius1:{r} radius2:{r2} pos:{pos}",
}


def max_create_object(
    kind: str,
    name: str = "",
    pos: list[float] | None = None,
    params: dict | None = None,
) -> dict:
    """Create a primitive, or import geometry from disk.

    kind: box | sphere | cylinder | plane | teapot | cone | torus | import
    size keys by kind -- box{w,l,h} sphere{r} cylinder{r,h} plane{w,l}
    teapot{r} cone{r,h} torus{r,r2}. For kind="import": {"path": "..."}.

    Args:
        kind: primitive type or "import".
        name: node name (ignored for import).
        pos: [x,y,z] world position.
        params: size parameters, see above.
    """
    kind_l = (kind or "").lower().strip()
    params = params or {}
    pos = pos or [0.0, 0.0, 0.0]

    if kind_l == "import":
        path = params.get("path", "")
        if not path:
            raise ValueError("kind='import' requires params={'path': ...}")
        script = f"""(
    local __p = {mxs_str(path)}
    if not (doesFileExist __p) do throw ("file not found: " + __p)
    local __b = objects.count
    importFile __p #noPrompt
    #("imported", (objects.count - __b), "path", __p)
)"""
        return flat_to_dict(call(script, timeout=600))

    if kind_l not in _PRIMITIVES:
        raise ValueError(
            f"unknown kind '{kind}', expected one of {sorted(_PRIMITIVES)} or 'import'"
        )

    fmt = {"w": 100.0, "l": 100.0, "h": 100.0, "r": 50.0, "r2": 10.0}
    for k, v in params.items():
        fmt[k] = float(v)

    body = _PRIMITIVES[kind_l].format(
        **fmt, pos=f"[{float(pos[0])}, {float(pos[1])}, {float(pos[2])}]"
    )

    script = f"""(
    local __n = {body}
    local __want = {mxs_str(name)}
    if __want != "" do ( try ( __n.name = __want ) catch() )
    #("created", __n.name, "class", ((classof __n) as string), "pos", __n.pos)
)"""
    return flat_to_dict(call(script, timeout=60))


def max_python(code: str, timeout_seconds: float = 30.0) -> Any:
    """Execute Python inside Max (2.7 + MaxPlus) -- a second engine, chosen explicitly.

    WHEN TO USE THIS (measured on real Max 2016, see tests/bench_*):
      - pure computation, string building, dict/list work: Python is 7-100x faster
        than MAXScript (100k loop 6 ms vs 55 ms; 5k string concat <1 ms vs 177 ms)
      - scene access is the opposite: creating 200 boxes MAXScript 30 ms vs
        MaxPlus ~260 ms; walking 200 nodes MAXScript 1 ms vs ~2 ms.
    So: never drive the scene from here. Use it for logic, parsing, data
    shaping, or MaxPlus-only APIs, then hand the result back.

    The script runs with `result` as the value that gets returned:
        result = [n.GetName() for n in ...]
    Set `result` on the last line; anything JSON-serializable works.

    CAVEATS on Max 2016: Python is 2.7 and the object API is MaxPlus, which
    Autodesk removed in 2017 (replaced by pymxs). Code written against MaxPlus
    will not survive a Max upgrade, unlike MAXScript.

    Args:
        code: Python source. Assign to `result` for the return value.
        timeout_seconds: how long to wait before giving up.
    """
    import base64
    import uuid

    os.makedirs(SHOT_DIR, exist_ok=True)
    _sweep_temp()
    rid = uuid.uuid4().hex[:12]
    script_path = os.path.join(SHOT_DIR, f"py_{rid}.py")
    out_path = os.path.join(SHOT_DIR, f"py_{rid}.json")

    # base64 keeps the payload ASCII, so no quoting/encoding can corrupt it.
    # Decode to a str first: Max 2016 runs Python 2.7 (where exec takes a
    # statement), Max 2025 runs Python 3.11 (where exec needs str, not bytes).
    # compile()+exec() as a call works on both.
    payload = base64.b64encode(code.encode("utf-8")).decode("ascii")
    wrapper = "\n".join([
        "import base64, json, sys, traceback",
        "_g = {'__name__': '__main__'}",
        "class _Cap(object):",
        "    def __init__(self): self.parts = []",
        "    def write(self, s):",
        "        if not isinstance(s, type(u'')):",
        "            try:",
        "                s = s.decode('utf-8', 'replace')",
        "            except Exception:",
        "                s = u'<undecodable>'",
        "        self.parts.append(s)",
        "    def flush(self): pass",
        "    def getvalue(self): return u''.join(self.parts)",
        "_o, _e = sys.stdout, sys.stderr",
        "_cap_o, _cap_e = _Cap(), _Cap()",
        "_res = {'ok': True, 'result': None, 'stdout': u'', 'stderr': u''}",
        "try:",
        "    _src = base64.b64decode({payload}).decode('utf-8')".format(payload=repr(payload)),
        "    sys.stdout, sys.stderr = _cap_o, _cap_e",
        "    try:",
        "        exec(compile(_src, '<mcp>', 'exec'), _g)",
        "        _res['result'] = _g.get('result')",
        "    finally:",
        "        sys.stdout, sys.stderr = _o, _e",
        "except Exception:",
        "    _res['ok'] = False",
        "    _res['error'] = traceback.format_exc()",
        "_res['stdout'] = _cap_o.getvalue()",
        "_res['stderr'] = _cap_e.getvalue()",
        "def _enc(x):",
        "    if isinstance(x, bytes):",
        "        for e in ('utf-8', 'gbk', 'latin-1'):",
        "            try:",
        "                return x.decode(e)",
        "            except Exception:",
        "                continue",
        "        return x.decode('utf-8', 'replace')",
        "    return x",
        "_res = dict((k, _enc(v)) for k, v in _res.items())",
        "json.dump(_res, open({out!r}, 'w'), default=_enc)".format(out=out_path),
    ]) + "\n"
    with open(script_path, "w", encoding="utf-8") as fh:
        fh.write(wrapper)

    p = script_path.replace("\\", "\\\\")
    envelope = None
    try:
        call(f'python.ExecuteFile @"{p}"', timeout=timeout_seconds)
        if os.path.isfile(out_path):
            with open(out_path, encoding="utf-8") as fh:
                envelope = json.load(fh)
    finally:
        # The wrapper script is safe to drop immediately: Max has already read
        # and compiled it.
        try:
            os.remove(script_path)
        except OSError:
            pass
        # The result file is NOT. On a timeout Max is still running and writes
        # out_path *after* we gave up; deleting it now only makes it reappear
        # later as an orphan nobody will ever look at again. Delete it solely
        # on the success path and let _sweep_temp() collect the rest.
        if envelope is not None:
            try:
                os.remove(out_path)
            except OSError:
                pass

    if envelope is None:
        raise MaxError(
            "Python produced no result file. The interpreter may be unavailable "
            "in this Max -- verify with `maxctl eval 'python.Execute \"1\"'`."
        )
    out = envelope.get("stdout") or ""
    err = envelope.get("stderr") or ""
    if not envelope.get("ok"):
        detail = envelope.get("error") or "python failed"
        if out.strip():
            detail += "\n--- stdout before the error ---\n" + out
        raise MaxError(detail)
    result = envelope.get("result")
    if out.strip() or err.strip():
        return {"result": result, "stdout": out, "stderr": err}
    return result


def max_api(class_name: str, pattern: str = "*") -> dict:
    """List the members a class DECLARES. Reflect instead of guessing.

    Model memory about MAXScript is unreliable and version-specific -- this
    session alone produced six wrong assumptions (BooleanClass vs Boolean,
    formattedPrint format strings, getNodeByName default args, node.layer,
    byte[].Length, case on strings).

    KNOWN BLIND SPOT: this lists only members declared on the class itself.
    Node-level universal properties -- `name`, `pos`, `wirecolor`, `isHidden`,
    `isFrozen`, `parent`, `material` -- are NOT in any property list, not even
    `getPropNames`, yet they work. For those, use `max_check` to test the
    expression directly. `showClass "*.*"` also never lists the abstract `node`
    class, so `max_api_search "wirecolor"` will not find them either.

    Args:
        class_name: class to inspect, e.g. "Box", "Editable_Poly", "VRayLight".
        pattern: member name filter, MAXScript wildcard. "*" lists everything,
                 "*segs" narrows to matching members (much shorter output).
    """
    os.makedirs(SHOT_DIR, exist_ok=True)
    path = os.path.join(SHOT_DIR, f"api_{uuid.uuid4().hex[:10]}.txt")
    p = path.replace("\\", "\\\\")

    script = f"""(
    local __f = createFile "{p}"
    local __ok = false
    try ( showClass "{mxs_str(class_name)[1:-1]}.{pattern}" to:__f ; __ok = true ) catch()
    try ( close __f ) catch()
    __ok
)"""
    ok = call(script, timeout=30)
    if not ok:
        raise MaxError(f"showClass failed for '{class_name}' -- class may not exist in this Max")

    text = ""
    for enc in ("gbk", "utf-8", "latin-1"):
        try:
            with open(path, encoding=enc) as fh:
                text = fh.read()
            break
        except (UnicodeDecodeError, LookupError):
            continue
    try:
        os.remove(path)
    except OSError:
        pass

    members: list[dict] = []
    header = ""
    superclass = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("."):
            m = re.match(r"\.([A-Za-z_][\w]*)(?:\([^)]*\))?\s*:\s*(\S+)", stripped)
            if m:
                members.append({"name": m.group(1), "type": m.group(2)})
            continue
        if not header and ":" in stripped:
            header = stripped
            parts = stripped.split(":", 1)
            superclass = parts[1].strip().split()[0] if len(parts) > 1 else ""

    return {
        "class": class_name,
        "superclass": superclass,
        "header": header,
        "member_count": len(members),
        "members": members,
        "note": "declared members only; node universals like name/pos/wirecolor need max_check",
    }


def max_check(expressions: list[str], timeout_seconds: float = 60.0) -> dict:
    """Try a list of MAXScript expressions and report which ones actually work.

    This is the counterpart to max_api's blind spot. Reflection cannot tell you
    whether `obj.wirecolor` or a plugin property exists, because those are not
    in any property list -- but evaluation can. Run them before writing code
    that depends on them.

    SAMPLES ARE CLEANED UP. Expressions run against the live scene, so a probe
    like `(Box()).wirecolor` really does create a box. Nodes created during the
    run are deleted afterwards and their names come back under
    `removed_side_effects`. Anything that could NOT be deleted -- a locked node,
    or one whose parent was already removed -- shows up under
    `leftover_side_effects`; a non-empty list there means the scene is dirty and
    you should say so instead of assuming the probe was free. Two consequences:

      * a probe whose whole point is to create something will not survive --
        check for the side effect by name in the `removed_side_effects` list
      * tracking is by scene callback (#nodeCreated), so it sees exactly what
        the probe created -- no whole-scene scan, and no chance of mistaking a
        pre-existing node for a new one

    Args:
        expressions: MAXScript expressions to evaluate in order, e.g.
                     ["(Box()).wirecolor", "(Box()).custom_attr", "units.SystemType"].
        timeout_seconds: how long to wait before giving up.
    """
    # Catch new nodes with a scene callback instead of diffing the whole scene.
    #
    # The obvious approach -- snapshot `objects` and compare afterwards -- is
    # O(N^2) in MAXScript (no hash tables) and measured at 30-120 s on a 50k
    # object scene, i.e. unusable on exactly the files where a mistake is most
    # expensive. #nodeCreated fires per created node and hands it over via
    # notificationParam(), so the cost is O(k) where k is what the probe built
    # (verified on 2016: the callback sees the new node itself, `$` does not).
    #
    # MAXScript has no try/finally, so the callback is torn down in three
    # places: defensively before installing, on the normal path, and again if
    # the guarded block throws. A leaked #nodeCreated callback would keep
    # appending every future node into a global array forever.
    script_parts = [
        "(",
        "    global __mcp_probe_new = #()",
        "    __mcp_probe_new = #()",
        "    local __res = #()",
        "    local __r",
        "    local __boom = \"\"",
        "    try ( callbacks.removeScripts id:#mcpProbe ) catch ()",
        # Must stay on ONE line: MAXScript ends the statement at the line break,
        # so `callbacks.addScript #nodeCreated` followed by the callback string
        # on the next line is a call with no arguments ("needs a string or
        # fileName: argument"). Verified on 2016.
        "    callbacks.addScript #nodeCreated \"append __mcp_probe_new (callbacks.notificationParam())\" id:#mcpProbe",
        "    try (",
    ]
    for expr in expressions:
        script_parts.append("    __r = #(false, \"\")")
        script_parts.append(
            f"    try ( ( {expr} ) ; __r = #(true, \"\") )"
            f" catch ( __r = #(false, (getCurrentException() as string)) )"
        )
        script_parts.append("    append __res __r")
    script_parts.append("    ) catch ( __boom = getCurrentException() as string )")
    script_parts.append("    try ( callbacks.removeScripts id:#mcpProbe ) catch ()")
    script_parts.append("    local __removed = #()")
    script_parts.append("    local __leftover = #()")
    script_parts.append("    local __nm")
    script_parts.append("    undo off ( for o in __mcp_probe_new do (")
    script_parts.append("        if (isValidNode o) do (")
    script_parts.append("            __nm = o.name")
    script_parts.append("            try (delete o) catch ()")
    script_parts.append(
        "            if (isValidNode o) then (append __leftover __nm)"
        " else (append __removed __nm)"
    )
    script_parts.append("        )")
    script_parts.append("    ) )")
    script_parts.append("    __mcp_probe_new = #()")
    script_parts.append("    #(__res, __removed, __leftover, __boom)")
    script_parts.append(")")
    script = "\n".join(script_parts)

    raw = call(script, timeout=timeout_seconds)
    results = raw[0] if isinstance(raw, list) and raw else []
    removed = raw[1] if isinstance(raw, list) and len(raw) > 1 else []
    leftover = raw[2] if isinstance(raw, list) and len(raw) > 2 else []
    boom = raw[3] if isinstance(raw, list) and len(raw) > 3 else ""
    out = []
    for i, expr in enumerate(expressions):
        entry = results[i] if isinstance(results, list) and i < len(results) else [False, "no result"]
        ok = bool(entry[0]) if isinstance(entry, list) else False
        err = entry[1] if isinstance(entry, list) and len(entry) > 1 else ""
        out.append({"expression": expr, "ok": ok, "error": None if ok else err})
    return {
        "checked": len(out),
        "results": out,
        "removed_side_effects": removed if isinstance(removed, list) else [],
        "leftover_side_effects": leftover if isinstance(leftover, list) else [],
        "harness_error": boom or None,
    }


def _api_index_path() -> str:
    return os.path.join(SHOT_DIR, f"api_index_{BRIDGE.port}.txt")


def max_api_search(keyword: str, limit: int = 60, refresh: bool = False) -> dict:
    """Search the ENTIRE API surface of the running Max -- classes and members.

    Dumps `showClass "*.*"` (measured: ~39,700 lines / 1.4 MB on 2016, 3.1 s;
    ~5.3 s on 2025) to a per-port cache, then greps it. This is the only way to
    answer "does this Max have VRayLight?" or "which classes expose wirecolor?"
    without guessing from model memory.

    Answers, for example:
        max_api_search "wirecolor"   -> every class exposing that property + type
        max_api_search "VRay"        -> which V-Ray classes are actually installed
        max_api_search "Alembic"     -> whether that importer exists here

    Args:
        keyword: case-insensitive substring matched against class and member names.
        limit: max matches to return.
        refresh: rebuild the cache even if one exists (after installing plugins).
    """
    path = _api_index_path()
    if refresh or not os.path.isfile(path):
        os.makedirs(SHOT_DIR, exist_ok=True)
        p = path.replace("\\", "\\\\")
        script = f"""(
    local __f = createFile "{p}"
    local __ok = false
    try ( showClass "*.*" to:__f ; __ok = true ) catch()
    try ( close __f ) catch()
    __ok
)"""
        call(script, timeout=180)

    if not os.path.isfile(path):
        raise MaxError("could not build the API index (showClass produced no file)")

    text = ""
    for enc in ("gbk", "utf-8", "latin-1"):
        try:
            with open(path, encoding=enc) as fh:
                text = fh.read()
            break
        except (UnicodeDecodeError, LookupError):
            continue

    kw = keyword.lower()
    matches: list[dict] = []
    truncated = False
    total_lines = 0
    current_class = ""
    for line in text.splitlines():
        total_lines += 1
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("."):
            m = re.match(r"\.([A-Za-z_][\w]*)(?:\([^)]*\))?\s*:\s*(\S+)", stripped)
            if not m:
                continue
            member = m.group(1)
            if kw in member.lower() or kw in current_class.lower():
                if len(matches) >= limit:
                    truncated = True
                    break
                matches.append({"class": current_class, "member": member, "type": m.group(2)})
            continue
        if ":" in stripped:
            current_class = stripped.split(":", 1)[0].split("(")[0].strip()

    return {
        "keyword": keyword,
        "index_lines": total_lines,
        "match_count": len(matches),
        "truncated": truncated,
        "matches": matches,
    }


_DOC_CACHE: dict[str, Any] = {}


def _docs_dir() -> str:
    """Condensed corpus: 2591 pages / 1.3 MB (15% of the raw crawl).

    Full text lives in docs/maxscript-2025 if a hit needs more context.
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    slim = os.path.join(root, "tools", "docs", "maxscript-2025-slim")
    raw = os.path.join(root, "tools", "docs", "maxscript-2025")
    return slim if os.path.isdir(slim) else raw


def max_doc(query: str, limit: int = 5, snippet_chars: int = 500) -> dict:
    """Search a local copy of the MAXScript Reference for a term.

    Why local: Autodesk retired the 2016 documentation entirely (cloudhelp/2016
    returns 404), so `max_api`-style reflection is the only ground truth for
    2016 specifics. The 2025 reference is still published and is the closest
    complete prose description of the language; it is crawled into
    tools/docs/maxscript-2025 by tools/crawl_maxscript_docs.py.

    Use this for "how do I use X" / "what are the arguments of Y". Use
    `max_api` / `max_check` for "does THIS Max have it" -- prose can be stale
    by a release, the running process cannot.

    Args:
        query: term to look for, case-insensitive (e.g. "polyop", "getNodeByName").
        limit: max matching pages.
        snippet_chars: characters of surrounding context per hit.
    """
    ddir = _docs_dir()
    if not os.path.isdir(ddir):
        raise MaxError(
            f"no local docs at {ddir}. Build them with:\n"
            "  python tools/crawl_maxscript_docs.py"
        )

    if "pages" not in _DOC_CACHE:
        pages = []
        for name in sorted(os.listdir(ddir)):
            if not name.endswith(".md"):
                continue
            try:
                with open(os.path.join(ddir, name), encoding="utf-8") as fh:
                    text = fh.read()
            except OSError:
                continue
            title = ""
            for line in text.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            source = ""
            m = re.search(r"<!-- source: (\S+) -->", text)
            if m:
                source = m.group(1)
            pages.append((name, title, text, source))
        _DOC_CACHE["pages"] = pages
        _DOC_CACHE["dir"] = ddir

    pages = _DOC_CACHE["pages"]
    q = query.lower()
    hits = []
    for name, title, text, source in pages:
        pos = text.lower().find(q)
        if pos < 0:
            continue
        start = max(0, pos - snippet_chars // 3)
        snippet = text[start : start + snippet_chars].replace("\n", " ").strip()
        hits.append(
            {
                "title": title or name,
                "file": name,
                "snippet": snippet,
                "source": source,
                "hits_in_page": text.lower().count(q),
            }
        )
    hits.sort(key=lambda h: -h["hits_in_page"])
    return {
        "query": query,
        "pages_searched": len(pages),
        "match_count": len(hits),
        "results": hits[:limit],
    }


# ---------------------------------------------------------------------------
# 8. delete
# ---------------------------------------------------------------------------


def max_delete_objects(names: list[str]) -> dict:
    """Delete nodes by name inside one undo block.

    Args:
        names: node names to delete.
    """
    if not names:
        raise ValueError("names must not be empty")
    script = f"""(
    local __names = {mxs_list(names)}
    local __gone  = #()
    local __miss  = #()

    undo "MCP: delete" on
    (
        for __nm in __names do
        (
{lookup_block("__o", "__nm", "d")}            if __o == undefined then ( append __miss __nm )
            else ( append __gone __o.name; delete __o )
        )
    )
    #("deleted", __gone.count, "deleted_names", __gone, "missing", __miss)
)"""
    return flat_to_dict(call(script, timeout=180))


# ---------------------------------------------------------------------------
# 9. screenshot
# ---------------------------------------------------------------------------


def capture_to_file(
    render: bool = False,
    width: int = 1280,
    height: int = 720,
    out_path: str | None = None,
) -> str:
    """Grab the viewport (or a quick render) into a PNG and return the path.

    Split out from max_screenshot so the CLI can write wherever the user asked
    instead of going through the MCP image content type.
    """
    if out_path:
        path = os.path.abspath(out_path)
        if os.path.isdir(path):
            raise MaxError(f"out_path is a directory, not a file: {path}")
        ext = os.path.splitext(path)[1].lower()
        # `save __bmp` writes image bytes regardless of the name, so a typo'd
        # extension silently destroys whatever text file was at that path.
        if ext not in _IMAGE_EXT:
            raise MaxError(
                f"unsupported image extension {ext or '(none)'!r}; use one of "
                + ", ".join(sorted(_IMAGE_EXT))
            )
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    else:
        os.makedirs(SHOT_DIR, exist_ok=True)
        path = os.path.join(SHOT_DIR, f"shot_{int(time.time() * 1000)}.png")

    p = path.replace("\\", "\\\\")

    if render:
        script = f"""(
    local __p = "{p}"
    local __bmp = render outputwidth:{int(width)} outputheight:{int(height)} vfb:false
    __bmp.filename = __p
    save __bmp
    try ( close __bmp ) catch()
    __p
)"""
    else:
        script = f"""(
    local __p = "{p}"
    local __bmp = undefined
    try ( __bmp = gw.getViewportDib() ) catch()
    if __bmp == undefined do throw "gw.getViewportDib() returned nothing - is a viewport active?"
    __bmp.filename = __p
    save __bmp
    try ( close __bmp ) catch()
    __p
)"""

    returned = call(script, timeout=180)
    if not os.path.isfile(path):
        raise MaxError(f"capture finished but no file at {path} (Max said {returned!r})")
    return path


def max_screenshot(render: bool = False, width: int = 1280, height: int = 720) -> Any:
    """Capture the active viewport (or a quick render) and return it as an image.

    Use this to verify a change actually looks right instead of inferring from
    coordinates. Rendering is slower (seconds) but respects camera and lights.

    Args:
        render: False = grab the viewport framebuffer (instant);
                True = render the active view (honours materials and lights).
        width: render width when render=True.
        height: render height when render=True.
    """
    from .compat import Image

    path = capture_to_file(render=render, width=width, height=height)
    # The bytes are read into memory for the MCP image content type, so the PNG
    # on disk has no further use. This path is always a temp file: max_screenshot
    # never passes out_path -- maxctl does that via capture_to_file directly.
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return Image(data=data, format="png")


# ---------------------------------------------------------------------------
# 10. file io
# ---------------------------------------------------------------------------


def max_file_io(
    action: str, path: str, selected_only: bool = False, overwrite: bool = False
) -> dict:
    """Import, export, or merge geometry files (FBX / OBJ / MAX).

    Export refuses to clobber an existing file unless `overwrite=True`. Exporting
    over the .max you are currently working on, or over the source you imported
    from, destroys it with no undo -- far too easy to do by accident, so the
    default is to fail loudly instead.

    Args:
        action: "import" | "export" | "merge".
        path: absolute file path.
        selected_only: export only the current selection.
        overwrite: allow export to replace an existing file.
    """
    act = (action or "").lower().strip()
    if act not in ("import", "export", "merge"):
        raise ValueError("action must be import | export | merge")

    target = os.path.abspath(path)
    # MAXScript's doesFileExist() returns true for DIRECTORIES as well, so the
    # guard inside the snippet cannot tell a folder from a file. Check here.
    if os.path.isdir(target):
        raise MaxError(f"path is a directory, not a file: {target}")
    if act == "export":
        if os.path.exists(target) and not overwrite:
            raise MaxError(
                f"refusing to overwrite an existing file: {target}\n"
                "Pass overwrite=true to replace it deliberately."
            )
        parent = os.path.dirname(target)
        if parent and not os.path.isdir(parent):
            raise MaxError(f"target directory does not exist: {parent}")
    elif not os.path.isfile(target):
        raise MaxError(f"file not found: {target}")

    if act == "import":
        script = f"""(
    local __p = {mxs_str(path)}
    if not (doesFileExist __p) do throw ("file not found: " + __p)
    local __b = objects.count
    importFile __p #noPrompt
    #("ok", true, "added", (objects.count - __b), "path", __p)
)"""
    elif act == "merge":
        script = f"""(
    local __p = {mxs_str(path)}
    if not (doesFileExist __p) do throw ("file not found: " + __p)
    local __b = objects.count
    mergeMAXFile __p #noPrompt
    #("ok", true, "added", (objects.count - __b), "path", __p)
)"""
    elif act == "export":
        flag = "true" if selected_only else "false"
        script = f"""(
    local __p = {mxs_str(path)}
    exportFile __p #noPrompt selectedOnly:{flag}
    local __sz = 0
    if (doesFileExist __p) do __sz = (getFileSize __p)
    #("ok", (doesFileExist __p), "path", __p, "bytes", __sz)
)"""
    else:
        raise ValueError("action must be import | export | merge")
    return flat_to_dict(call(script, timeout=600))


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def register(mcp) -> None:
    for fn in (
        max_status,
        max_eval,
        max_python,
        max_api,
        max_api_search,
        max_check,
        max_doc,
        max_scene_summary,
        max_query_objects,
        max_get_object,
        max_set_property,
        max_create_object,
        max_delete_objects,
        max_screenshot,
        max_file_io,
    ):
        mcp.tool()(fn)
