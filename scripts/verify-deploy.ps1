[CmdletBinding()]
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

$results = [System.Collections.Generic.List[pscustomobject]]::new()

function Add-CheckResult {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$Detail
    )

    $results.Add([pscustomobject]@{
            Name   = $Name
            Passed = $Passed
            Detail = $Detail
        })
}

function Invoke-Check {
    param(
        [string]$Name,
        [scriptblock]$Action
    )

    try {
        $detail = & $Action
        Add-CheckResult -Name $Name -Passed $true -Detail ([string]$detail)
    }
    catch {
        Add-CheckResult -Name $Name -Passed $false -Detail $_.Exception.Message
    }
}

function Get-EnvironmentConfigPaths {
    param([string]$AzureDirectory)

    Get-ChildItem -LiteralPath $AzureDirectory -Directory |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'config.json') } |
        ForEach-Object { Join-Path $_.FullName 'config.json' }
}

function Get-HookScriptPaths {
    param([string]$AzureYamlPath)

    $content = Get-Content -LiteralPath $AzureYamlPath -Raw
    [regex]::Matches($content, '[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.ps1') |
        ForEach-Object { $_.Value } |
        Sort-Object -Unique
}

$requiredParameters = @(
    'HARNESS_MCP_ENTRA_TENANT_ID',
    'HARNESS_MCP_ENTRA_AUDIENCE',
    'HARNESS_MCP_ENTRA_CLIENT_ID',
    'HARNESS_ADMIN_TOKEN',
    'githubWebhookSecret'
)

$repoRootPath = (Resolve-Path -LiteralPath $RepoRoot).Path
$azureYamlPath = Join-Path $repoRootPath 'azure.yaml'
$mainBicepPath = Join-Path $repoRootPath 'infra\main.bicep'
$azureDirectory = Join-Path $repoRootPath '.azure'

Invoke-Check -Name 'azd provision help' -Action {
    $output = & azd provision --help 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "azd provision --help failed: $($output -join [Environment]::NewLine)"
    }

    'azd help OK'
}

Invoke-Check -Name 'az bicep build' -Action {
    $output = & az bicep build --file $mainBicepPath 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "az bicep build failed: $($output -join [Environment]::NewLine)"
    }

    'infra/main.bicep builds'
}

Invoke-Check -Name 'azd environment parameters' -Action {
    if (-not (Test-Path -LiteralPath $azureDirectory)) {
        throw '.azure directory not found'
    }

    $configPaths = @(Get-EnvironmentConfigPaths -AzureDirectory $azureDirectory)
    if ($configPaths.Count -eq 0) {
        throw 'No .azure/*/config.json environment files found'
    }

    $messages = foreach ($configPath in $configPaths) {
        $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json -AsHashtable
        $parameters = $config['infra']['parameters']
        if (-not $parameters) {
            throw "$configPath missing infra.parameters"
        }

        $missing = @($requiredParameters | Where-Object { -not $parameters.ContainsKey($_) -or [string]::IsNullOrWhiteSpace([string]$parameters[$_]) })
        if ($missing.Count -gt 0) {
            throw "$configPath missing: $($missing -join ', ')"
        }

        "$(Split-Path -Leaf (Split-Path -Parent $configPath)): OK"
    }

    $messages -join '; '
}

Invoke-Check -Name 'azure.yaml hook scripts' -Action {
    if (-not (Test-Path -LiteralPath $azureYamlPath)) {
        throw 'azure.yaml not found'
    }

    $relativePaths = @(Get-HookScriptPaths -AzureYamlPath $azureYamlPath)
    if ($relativePaths.Count -eq 0) {
        throw 'No PowerShell hook scripts referenced in azure.yaml'
    }

    $missing = foreach ($relativePath in $relativePaths) {
        $fullPath = Join-Path $repoRootPath $relativePath
        if (-not (Test-Path -LiteralPath $fullPath)) {
            $relativePath
        }
    }

    if ($missing.Count -gt 0) {
        throw "Missing hook scripts: $($missing -join ', ')"
    }

    $relativePaths -join ', '
}

$failed = @($results | Where-Object { -not $_.Passed })
$passed = @($results | Where-Object { $_.Passed })

foreach ($result in $results) {
    $status = if ($result.Passed) { 'PASS' } else { 'FAIL' }
    Write-Host "[$status] $($result.Name): $($result.Detail)"
}

Write-Host ''
Write-Host "Summary: $($passed.Count) passed, $($failed.Count) failed"

if ($failed.Count -gt 0) {
    exit 1
}

exit 0
