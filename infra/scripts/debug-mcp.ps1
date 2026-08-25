param(
    [string] $ResourceGroup = 'rg-harness-dev-lm',
    [string] $AppName = 'ca-harness-dev-lm-mcpserver',
    [string] $Revision
)
$ErrorActionPreference = 'Continue'
$py = @'
import urllib.request as u, sys
try:
    print('local8000:', u.urlopen('http://127.0.0.1:8000/healthz', timeout=5).status)
except Exception as e:
    print('local8000 FAIL:', e)
try:
    import mcpserver
    print('import mcpserver: ok')
except Exception as e:
    print('import mcpserver FAIL:', type(e).__name__, e)
try:
    app = mcpserver.create_mcp_app()
    print('factory ok:', app)
except Exception as e:
    print('factory FAIL:', type(e).__name__, e)
'@
$args = @('-c', $py)
az containerapp exec -g $ResourceGroup -n $AppName $(if ($Revision) { @('--revision', $Revision) }) --command python @args 2>&1 | Out-String -Width 200
