-- ============================================================================
--  max-mcp :: one-shot installer
--  Run inside Max: Scripting > Run Script... > this file.
--  Copies max_mcp_bridge.ms into the per-user startup folder so the bridge
--  comes back every time Max launches, then starts it immediately.
-- ============================================================================
(
    local __src = getFilenamePath (getSourceFileName())
    local __bridge = __src + "max_mcp_bridge.ms"

    if not (doesFileExist __bridge) then
    (
        messageBox "max_mcp_bridge.ms not found next to install.ms" title:"max-mcp"
    )
    else
    (
        local __dst = undefined
        try ( __dst = getDir #userStartupScripts ) catch()
        if __dst == undefined do __dst = (getDir #userScripts) + "\\startup"
        if (doesFileExist __dst) == false do makeDir __dst

        local __b = __dst + "\\max_mcp_bridge.ms"
        local __ok = copyFile __bridge __b

        format "[max-mcp] startup dir: %\n" __dst
        format "[max-mcp] bridge -> % (%)\n" __b __ok

        -- start it right now so a restart is not required
        try ( fileIn __b ) catch ( format "[max-mcp] load failed: %\n" (getCurrentException()) )

        messageBox ("Installed to:\n" + __dst + "\n\nThe bridge is running now.\nPress F11 to check the MAXScript Listener.") title:"max-mcp"
    )
)
