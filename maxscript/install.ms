-- Run in Max: Scripting > Run Script. Installs into this Max instance.
-- Source and destination are derived at runtime, without machine paths.
(
    local sourceDir = getFilenamePath (getSourceFileName())
    local sourceBridge = pathConfig.appendPath sourceDir "max_mcp_bridge.ms"
    local sourceLauncher = pathConfig.appendPath sourceDir "start_maxmcp.ms"
    local scriptsDir = getDir #scripts
    local payloadDir = pathConfig.appendPath scriptsDir "maxmcp"
    local startupDir = pathConfig.appendPath scriptsDir "startup"
    local installedBridge = pathConfig.appendPath payloadDir "max_mcp_bridge.ms"
    local installedLauncher = pathConfig.appendPath startupDir "start_maxmcp.ms"
    local failure = undefined
    try
    (
        if not (doesFileExist sourceBridge) do throw "Missing max_mcp_bridge.ms next to installer"
        if not (doesFileExist sourceLauncher) do throw "Missing start_maxmcp.ms next to installer"
        makeDir payloadDir all:true
        makeDir startupDir all:true
        local files = dotNetClass "System.IO.File"
        files.Copy sourceBridge installedBridge true
        files.Copy sourceLauncher installedLauncher true
        fileIn installedBridge
        local stale = pathConfig.appendPath startupDir "max_mcp_bridge.ms"
        if doesFileExist stale do deleteFile stale
    )
    catch (failure = getCurrentException())
    if failure == undefined then
        messageBox ("Installed and started in:\n" + scriptsDir) title:"max-mcp"
    else
        messageBox ("Installation failed:\n" + (failure as string) + "\nUse setup.ps1 if this Max scripts folder requires elevated access.") title:"max-mcp"
)
