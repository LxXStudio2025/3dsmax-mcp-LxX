-- ============================================================================
--  max2016-mcp :: in-Max bridge listener
--  Target : Autodesk 3ds Max 2016 (64-bit, .NET 4.x)
--  Deps   : none (pure MAXScript + dotNet, NO Python required inside Max)
--
--  Polls http://127.0.0.1:<port>/poll every 100 ms.
--    header X-Task-Id : "-" = idle, otherwise a task id
--    body             : raw MAXScript source, executed as-is
--  Posts {"ok":bool,"data":any,"error":str} back to /result.
--
--  Why polling and not a socket listener: MAXScript owns Max's single UI
--  thread and .NET async callbacks cannot safely re-enter script code, so Max
--  pulls work instead of accepting connections. A tick is ~0.3 ms on loopback.
--
--  STYLE NOTE: this file deliberately uses the most boring MAXScript available.
--  No `case` on strings (only ints/names are reliable), no try/catch used as an
--  expression, no one-line `if ... do try ...`. Every construct here compiles
--  on 2016; clever versions do not.
-- ============================================================================

global __MAXMCP_VER      = "0.3.5"
-- One port per Max release so several sessions can run side by side without
-- stealing each other's tasks: 2016 -> 47100, 2025 -> 47109, 2027 -> 47111.
global __MAXMCP_YEAR     = ((((maxVersion())[1]) / 1000) + 1998) as integer
global __MAXMCP_PORT     = (47100 + (amax 0 (__MAXMCP_YEAR - 2016)))
global __MAXMCP_URL      = "http://127.0.0.1:" + (__MAXMCP_PORT as string)
global __MAXMCP_TIMER    = undefined
global __MAXMCP_BUSY     = false
global __MAXMCP_SERVED   = 0
global __MAXMCP_LASTERR  = undefined
global __MAXMCP_LASTRES  = undefined
global __MAXMCP_MAXOUT   = 3000000
global __MAXMCP_DRAIN    = 8
-- Adaptive polling (borrowed from dcc-bridge's idle/active split): a fixed
-- 100 ms tick is pure waste when nobody is talking to us. Drop to 1 s after a
-- few empty rounds, snap back to 100 ms the moment a task arrives.
global __MAXMCP_ACTIVE_MS  = 100
global __MAXMCP_IDLE_MS    = 2000
global __MAXMCP_IDLE_TICKS = 0
-- HTTP timeouts are state-dependent for a reason. Connecting to a port nobody
-- listens on costs ~2050 ms on Windows (SYN retransmits) before refusing, and
-- the timer runs on Max's UI thread -- so a 3000 ms timeout means the UI blocks
-- ~2 s out of every 3 s while no MCP client is attached. Measured:
--   WebRequest.Timeout=200 -> returns in 216 ms, =500 -> 519 ms. It honours it.
-- So: short while idle (nobody is listening anyway), long once work is flowing.
global __MAXMCP_HTTP_T_ACTIVE = 3000
global __MAXMCP_HTTP_T_IDLE   = 250
global __MAXMCP_COLD_POLLS    = 3
global __MAXMCP_POLL_COUNT    = 0
global __MAXMCP_BOOTLOG  = ((sysInfo.tempdir) + "maxmcp-boot-" + (__MAXMCP_YEAR as string) + ".log")
global __MAXMCP_LASTLOG  = ""


-- ---------------------------------------------------------------------------
--  boot log -- the only way to tell "compile failed" from "runtime failed"
--  inside Max without a human reading the Listener.
-- ---------------------------------------------------------------------------

fn maxMcpLog msg =
(
    local h = undefined
    try ( h = openFile __MAXMCP_BOOTLOG mode:"a" ) catch()
    if h != undefined then
    (
        try ( format "[%] %\n" ((timeStamp()) as string) (msg as string) to:h ) catch()
        try ( close h ) catch()
    )
    ok
)

maxMcpLog "=== file execution started ==="

-- The poller hits a refused connection every 100 ms while the MCP server is
-- not running; logging that verbatim would grow the file without bound.
fn maxMcpLogErr msg =
(
    global __MAXMCP_LASTLOG
    local m = msg as string
    if m != __MAXMCP_LASTLOG do
    (
        __MAXMCP_LASTLOG = m
        maxMcpLog ("error: " + m)
    )
    ok
)


-- ---------------------------------------------------------------------------
--  JSON encoder. Encode only -- Max never parses JSON.
--  Dispatch is if/else on the class NAME STRING: an unknown class identifier
--  would be a compile error, a mismatched string is just a miss.
-- ---------------------------------------------------------------------------

fn maxMcpEsc s =
(
    local r = ""
    local ok = true
    try ( r = s as string ) catch ( ok = false )
    if ok == false do r = ""
    if r == "" then
    (
        r = ""
    )
    else
    (
        r = substituteString r "\\" "\\\\"
        r = substituteString r "\"" "\\\""
        r = substituteString r "\n" "\\n"
        r = substituteString r "\r" "\\r"
        r = substituteString r "\t" "\\t"
    )
    r
)

fn maxMcpNum v =
(
    local f = 0.0
    local isNum = true
    try ( f = v as float ) catch ( isNum = false )

    -- formattedPrint format:"%.7f" silently returns the format string itself in
    -- MaxScript, so rely on the native float->string conversion instead. It
    -- gives ~7 significant digits, which is plenty for scene coordinates.
    local s = "0.0"
    if isNum then
    (
        try ( s = (f as string) ) catch ( s = "0.0" )
        if s == "" do s = "0.0"
        if s == "-0.0" do s = "0.0"

        -- keep JSON numeric: add a decimal part when it printed as an integer
        if (findString s ".") == undefined do
        (
            if (findString s "e") == undefined and (findString s "E") == undefined do s = s + ".0"
        )
    )
    s
)

fn maxMcpJoin parts sep =
(
    local out = ""
    local n = parts.count
    for i = 1 to n do
    (
        local piece = ""
        try ( piece = parts[i] as string ) catch ( piece = "" )
        out += piece
        if i < n do out += sep
    )
    out
)

fn maxMcpJson v depth:0 maxItems:2000 =
(
    -- Single-exit on purpose. `return` inside a `for ... collect` body returns
    -- from the collect closure, not from this function, so an array containing
    -- undefined came back as undefined and blew up with
    -- "type conversion needs a class parameter, got: undefined".
    local out = "null"

    if depth > 10 then
    (
        out = "\"<max-depth>\""
    )
    else if v == undefined then
    (
        out = "null"
    )
    else
    (
        local cn = "Unknown"
        try ( cn = ((classOf v) as string) ) catch ( cn = "Unknown" )

        -- classOf true as string yields "BooleanClass", not "Boolean".
        -- `v as boolean` throws on an actual boolean and the catch silently
        -- turned every true into false, so compare instead of converting.
        if cn == "Boolean" or cn == "BooleanClass" then
        (
            if v == true then out = "true" else out = "false"
        )
        else if cn == "Integer" or cn == "Int64" then
        (
            local iv = 0
            try ( iv = v as integer ) catch ( iv = 0 )
            out = (iv as string)
        )
        else if cn == "Float" or cn == "Double" then
        (
            out = maxMcpNum v
        )
        else if cn == "String" then
        (
            out = "\"" + (maxMcpEsc v) + "\""
        )
        else if cn == "Name" then
        (
            local ns = ""
            try ( ns = v as string ) catch ( ns = "" )
            out = "\"" + (maxMcpEsc ns) + "\""
        )
        else if cn == "Point3" then
        (
            out = "[" + (maxMcpNum v.x) + "," + (maxMcpNum v.y) + "," + (maxMcpNum v.z) + "]"
        )
        else if cn == "Point2" then
        (
            out = "[" + (maxMcpNum v.x) + "," + (maxMcpNum v.y) + "]"
        )
        else if cn == "Color" then
        (
            out = "[" + ((v.r as integer) as string) + "," + ((v.g as integer) as string) + "," + ((v.b as integer) as string) + "]"
        )
        else if cn == "Matrix3" then
        (
            out = "[" + (maxMcpJson v.row1 depth:(depth + 1) maxItems:maxItems) + "," + \
                        (maxMcpJson v.row2 depth:(depth + 1) maxItems:maxItems) + "," + \
                        (maxMcpJson v.row3 depth:(depth + 1) maxItems:maxItems) + "," + \
                        (maxMcpJson v.row4 depth:(depth + 1) maxItems:maxItems) + "]"
        )
        else if cn == "BitArray" then
        (
            local bs = ""
            try ( bs = v as string ) catch ( bs = "" )
            out = "\"" + (maxMcpEsc bs) + "\""
        )
        else if cn == "Time" then
        (
            local tv = 0
            try ( tv = v as integer ) catch ( tv = 0 )
            out = (tv as string)
        )
        else if cn == "Array" then
        (
            if v.count == 0 then
            (
                out = "[]"
            )
            else
            (
                local n = v.count
                if n > maxItems do n = maxItems
                local parts = for i = 1 to n collect (maxMcpJson v[i] depth:(depth + 1) maxItems:maxItems)
                out = "[" + (maxMcpJoin parts ",") + "]"
            )
        )
        else
        (
            local s = ""
            local got = false
            try ( s = v.name as string; got = true ) catch()
            if got == false do try ( s = v as string; got = true ) catch()
            if got == false do s = "<unserializable>"
            out = "\"" + (maxMcpEsc s) + "\""
        )
    )

    out
)


-- ---------------------------------------------------------------------------
--  loopback HTTP
-- ---------------------------------------------------------------------------

fn maxMcpPrepare req timeout:500 =
(
    req.Timeout = timeout
    req.ReadWriteTimeout = timeout
    try ( req.KeepAlive = false ) catch()
    try ( req.Proxy = (dotNetClass "System.Net.GlobalProxySelection").GetEmptyWebProxy() ) catch()
    req
)

fn maxMcpHttpGet url timeout:500 =
(
    local req = (dotNetClass "System.Net.WebRequest").Create url
    req.Method = "GET"
    maxMcpPrepare req timeout:timeout

    local resp = req.GetResponse()
    local tid = "-"
    try ( tid = (resp.Headers).Get "X-Task-Id" ) catch()
    if tid == undefined do tid = "-"

    local enc = (dotNetClass "System.Text.Encoding").UTF8
    local sr = dotNetObject "System.IO.StreamReader" (resp.GetResponseStream()) enc
    local body = sr.ReadToEnd()
    try ( sr.Close() ) catch()
    try ( resp.Close() ) catch()
    #(tid, body)
)

fn maxMcpHttpPost url body tid timeout:5000 =
(
    -- WebClient.UploadString does length + encoding + stream writing in one
    -- call. Doing it by hand is a trap: HttpWebRequest.ContentLength needs the
    -- byte count of a byte[], and MaxScript cannot read .Length off the array
    -- that Encoding.GetBytes returns (it reports "unknown property: length"),
    -- so the body gets truncated to a single byte and Python sees "{".
    local enc = dotNetObject "System.Text.UTF8Encoding" false
    local wc = dotNetObject "System.Net.WebClient"
    wc.Encoding = enc
    try ( (wc.Headers).Set "X-Task-Id" (tid as string) ) catch()
    try ( wc.Proxy = (dotNetClass "System.Net.GlobalProxySelection").GetEmptyWebProxy() ) catch()

    local r = wc.UploadString url "POST" (body as string)
    try ( wc.Dispose() ) catch()
    true
)


-- ---------------------------------------------------------------------------
--  one task
-- ---------------------------------------------------------------------------

fn maxMcpRunOnce =
(
    global __MAXMCP_POLL_COUNT, __MAXMCP_SERVED, __MAXMCP_LASTRES
    -- Cold start needs the long timeout (first WebRequest pays proxy detection);
    -- after that, idle polls use a short one so a missing server cannot freeze UI.
    local __tmo = __MAXMCP_HTTP_T_ACTIVE
    if __MAXMCP_POLL_COUNT > __MAXMCP_COLD_POLLS and __MAXMCP_IDLE_TICKS > 3 do
    (
        __tmo = __MAXMCP_HTTP_T_IDLE
        -- Escape hatch. Leaving the idle tier requires served > 0, which requires
        -- one /poll to finish inside the short timeout. If the client ever gets
        -- slow (long-polling added, machine loaded, GIL contention), every poll
        -- would time out at 250 ms, keep IDLE_TICKS climbing, and stay pinned in
        -- the short-timeout tier forever -- the more it fails the shorter it
        -- waits. So every 10th idle round gets the full timeout: a self-healing
        -- probe that costs one slow round per ~20 s only in that broken state.
        if (mod __MAXMCP_POLL_COUNT 10) == 0 do __tmo = __MAXMCP_HTTP_T_ACTIVE
    )
    __MAXMCP_POLL_COUNT += 1
    local r = maxMcpHttpGet (__MAXMCP_URL + "/poll") timeout:__tmo
    local tid = r[1]
    local code = r[2]

    if tid == "-" do return false
    if tid == undefined do return false
    if tid == "" do return false
    if code == undefined do
    (
        maxMcpLog ("poll: task " + tid + " but empty body")
        return false
    )
    if (code as string).count == 0 do
    (
        maxMcpLog ("poll: task " + tid + " but zero-length script")
        return false
    )

    -- container, so values survive the try/catch boundary: [ok, result, error]
    local slot = #(true, undefined, undefined)
    try ( slot[2] = execute code )
    catch ( slot[1] = false; slot[3] = getCurrentException() )

    local json = ""
    if slot[1] then
    (
        try ( json = "{\"ok\":true,\"data\":" + (maxMcpJson slot[2]) + "}" )
        catch ( slot[1] = false; slot[3] = "serialize failed: " + (getCurrentException()) )
    )
    if slot[1] == false do
    (
        if json == "" do json = "{\"ok\":false,\"error\":" + (maxMcpJson (slot[3] as string)) + "}"
    )

    if json.count > __MAXMCP_MAXOUT do
    (
        json = "{\"ok\":false,\"error\":\"result too large (" + (json.count as string) + \
               " chars, limit " + (__MAXMCP_MAXOUT as string) + "). Narrow the query.\"}"
    )

    try ( maxMcpHttpPost (__MAXMCP_URL + "/result") json tid timeout:10000 )
    catch ( maxMcpLog ("POST failed for task " + tid + ": " + (getCurrentException())) )

    __MAXMCP_SERVED += 1
    __MAXMCP_LASTRES = slot[2]
    true
)

fn maxMcpTickBody =
(
    local served = 0
    for i = 1 to __MAXMCP_DRAIN do
    (
        if (maxMcpRunOnce()) == false do exit
        served += 1
    )
    served
)

fn maxMcpAdapt served =
(
    global __MAXMCP_IDLE_TICKS
    if __MAXMCP_TIMER == undefined then ()
    else if served > 0 then
    (
        __MAXMCP_IDLE_TICKS = 0
        if __MAXMCP_TIMER.Interval != __MAXMCP_ACTIVE_MS do
            __MAXMCP_TIMER.Interval = __MAXMCP_ACTIVE_MS
    )
    else
    (
        __MAXMCP_IDLE_TICKS += 1
        if __MAXMCP_IDLE_TICKS > 5 and __MAXMCP_TIMER.Interval != __MAXMCP_IDLE_MS do
            __MAXMCP_TIMER.Interval = __MAXMCP_IDLE_MS
    )
    ok
)

fn maxMcpTick sender ev =
(
    global __MAXMCP_BUSY, __MAXMCP_LASTERR
    if __MAXMCP_BUSY do return undefined
    __MAXMCP_BUSY = true
    local served = 0
    try ( served = maxMcpTickBody() )
    catch ( __MAXMCP_LASTERR = getCurrentException(); maxMcpLogErr (__MAXMCP_LASTERR as string) )
    __MAXMCP_BUSY = false
    -- Outside the try on purpose: a refused connection (server not running) is
    -- exactly when we most want to back off, and that path throws.
    maxMcpAdapt served
    ok
)


-- ---------------------------------------------------------------------------
--  lifecycle
-- ---------------------------------------------------------------------------

fn maxMcpStop =
(
    global __MAXMCP_TIMER
    if __MAXMCP_TIMER != undefined do
    (
        try ( __MAXMCP_TIMER.Stop() ) catch()
        try ( dotNet.removeEventHandler __MAXMCP_TIMER "Tick" ) catch()
        __MAXMCP_TIMER = undefined
    )
    format "[max2016-mcp] bridge stopped\n"
    true
)

fn maxMcpStart port:undefined quiet:false =
(
    global __MAXMCP_PORT, __MAXMCP_URL, __MAXMCP_BUSY, __MAXMCP_TIMER
    -- Resolve inside the body, not as a default argument: defaults are evaluated
    -- in the caller's scope where a per-release port may not be visible.
    if port == undefined do port = __MAXMCP_PORT
    try ( dotNet.loadAssembly "System.Windows.Forms" ) catch()

    maxMcpStop()
    __MAXMCP_PORT = port
    __MAXMCP_URL = "http://127.0.0.1:" + (port as string)
    __MAXMCP_BUSY = false

    local slot = #(undefined, undefined)   -- [timer, error]
    try
    (
        local t = dotNetObject "System.Windows.Forms.Timer"
        t.Interval = 100
        -- Without this the .NET timer can be collected by MaxScript's lifetime
        -- manager and silently stops ticking. Learned from cl0nazepamm/3dsmax-mcp.
        try ( dotNet.setLifetimeControl t #dotnet ) catch()
        dotNet.addEventHandler t "Tick" maxMcpTick
        t.Start()
        slot[1] = t
    )
    catch ( slot[2] = getCurrentException() )

    if slot[1] == undefined then
    (
        format "[max2016-mcp] START FAILED: %\n" (slot[2] as string)
        maxMcpLog ("start failed: " + (slot[2] as string))
        false
    )
    else
    (
        __MAXMCP_TIMER = slot[1]
        if not quiet do
        (
            format "[max2016-mcp] v% polling %/poll (adaptive %-% ms, drain %)\n" __MAXMCP_VER __MAXMCP_URL __MAXMCP_ACTIVE_MS __MAXMCP_IDLE_MS __MAXMCP_DRAIN
        )
        maxMcpLog ("started, polling " + __MAXMCP_URL)
        true
    )
)

fn maxMcpStatus =
(
    local err = ""
    if __MAXMCP_LASTERR != undefined do err = (__MAXMCP_LASTERR as string)
    #(
        "version", __MAXMCP_VER,
        "url", __MAXMCP_URL,
        "running", (__MAXMCP_TIMER != undefined),
        "served", __MAXMCP_SERVED,
        "busy", __MAXMCP_BUSY,
        "last_error", err,
        "boot_log", __MAXMCP_BOOTLOG
    )
)

fn maxMcpSelfTest =
(
    local out = #()
    append out ("timer=" + (if __MAXMCP_TIMER != undefined then "running" else "STOPPED"))
    append out ("json_point3=" + (maxMcpJson [1, 2, 3]))
    append out ("json_string=" + (maxMcpJson "a\"b"))
    append out ("json_array=" + (maxMcpJson #(1, "x", true)))
    append out ("json_float=" + (maxMcpJson 1.5))
    if objects.count > 0 do append out ("json_node=" + (maxMcpJson objects[1]))
    append out ("served=" + (__MAXMCP_SERVED as string))
    for line in out do ( format "[max2016-mcp] %\n" line )
    out
)


-- ---------------------------------------------------------------------------
--  boot
-- ---------------------------------------------------------------------------
-- MAXMCP_AUTOSTART=0 keeps the functions and the MaxMcpToggle macro available
-- but leaves the timer off, so a session stays completely untouched until you
-- start it yourself.
fn maxMcpAutostartWanted =
(
    local raw = undefined
    try ( raw = systemTools.getEnvVariable "MAXMCP_AUTOSTART" ) catch()
    if raw == undefined do return true
    local v = toLower (trimLeft (trimRight (raw as string)))
    not (v == "0" or v == "false" or v == "off" or v == "no")
)

maxMcpLog "all functions defined"
if (maxMcpAutostartWanted()) then
(
    maxMcpStart port:__MAXMCP_PORT
)
else
(
    format "[max2016-mcp] v% loaded but NOT started (MAXMCP_AUTOSTART=0). Use MaxMcpToggle or maxMcpStart()\n" __MAXMCP_VER
    maxMcpLog "autostart disabled by MAXMCP_AUTOSTART"
)
maxMcpLog "=== boot finished ==="
ok

-- ---------------------------------------------------------------------------
--  User-facing switch
--
--  The bridge has to be loaded before a client can reach it (Max polls, the
--  client cannot wake a non-listening process), so this is opt-out rather than
--  opt-in. These entries give you the off switch without editing scripts:
--    * a macroScript you can drag onto a toolbar / bind to a shortcut
--    * maxMcpStop() / maxMcpStart() callable from the MAXScript Listener
--  Set the environment variable MAXMCP_AUTOSTART=0 to skip autostart entirely
--  and only load the functions + macros.
-- ---------------------------------------------------------------------------

macroScript MaxMcpToggle
    category:"max2016-mcp"
    buttonText:"MCP Bridge"
    tooltip:"Start or stop the max2016-mcp bridge for this session"
(
    if __MAXMCP_TIMER == undefined then
    (
        maxMcpStart()
        format "[max2016-mcp] bridge started manually\n"
    )
    else
    (
        maxMcpStop()
        format "[max2016-mcp] bridge stopped manually -- the MCP client can no longer reach Max\n"
    )
    ok
)

macroScript MaxMcpShowStatus
    category:"max2016-mcp"
    buttonText:"MCP Status"
    tooltip:"Show bridge state: port, running, served count, last error"
(
    if __MAXMCP_TIMER == undefined and __MAXMCP_VER == undefined then
        messageBox "max2016-mcp is not loaded in this session." title:"MCP Status"
    else
    (
        local s = maxMcpStatus()
        local out = ""
        for i = 1 to s.count by 2 do
            out += (s[i] as string) + " = " + (s[i + 1] as string) + "\n"
        messageBox out title:"max2016-mcp status"
    )
    ok
)

macroScript MaxMcpRunSelfTest
    category:"max2016-mcp"
    buttonText:"MCP Self Test"
    tooltip:"Verify the JSON encoder and timer state"
(
    maxMcpSelfTest()
    ok
)
