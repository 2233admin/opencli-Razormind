param(
    [Parameter(Mandatory = $true)]
    [string]$CentralApiUrl,
    [string]$ApiAuthToken = "",
    [Parameter(Mandatory = $true)]
    [string]$OhMyOpenCliRepo,
    [string]$OhMyOpenCliRoot = "$env:LOCALAPPDATA\opencli-admin\OhMyOpenCLI"
)

$ErrorActionPreference = "Stop"
$OpenCliVersion = "1.8.5"
$OhMyOpenCliCommit = "73cc60c83586ef2c95469b3b70d6cfc80fa5bc53"
$CapabilitySourceCommit = "73cc60c83586ef2c95469b3b70d6cfc80fa5bc53"
$requestHeaders = @{}
if ($ApiAuthToken) {
    $requestHeaders = @{ Authorization = "Bearer $ApiAuthToken" }
}

foreach ($command in @("node", "npm", "git")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "$command is required"
    }
}

npm install -g "@jackwener/opencli@$OpenCliVersion"
$patchPath = Join-Path $env:TEMP "opencli-admin-patch-opencli.js"
Invoke-WebRequest `
    -UseBasicParsing `
    -Uri "$($CentralApiUrl.TrimEnd('/'))/api/v1/nodes/install/patch-opencli.js" `
    -Headers $requestHeaders `
    -OutFile $patchPath
node $patchPath

if (Test-Path $OhMyOpenCliRoot) {
    throw "Target already exists; choose a new OhMyOpenCliRoot or archive it explicitly: $OhMyOpenCliRoot"
}
git clone $OhMyOpenCliRepo $OhMyOpenCliRoot
git -C $OhMyOpenCliRoot checkout --detach $OhMyOpenCliCommit
git -C $OhMyOpenCliRoot merge-base --is-ancestor $CapabilitySourceCommit HEAD
if ($LASTEXITCODE -ne 0) {
    throw "official-site capability source commit is absent from the pinned checkout"
}
Push-Location $OhMyOpenCliRoot
try {
    npm ci
    npm run bootstrap
} finally {
    Pop-Location
}

[Environment]::SetEnvironmentVariable(
    "OHMYOPENCLI_ROOT", $OhMyOpenCliRoot, "User"
)
Write-Output "Managed OpenCLI runtime installed. Start the Agent with an explicit OPENCLI_BROWSER_PROFILE_KIND."
