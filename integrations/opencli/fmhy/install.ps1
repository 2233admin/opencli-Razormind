[CmdletBinding()]
param(
    [switch]$Force
)

$destination = Join-Path $env:USERPROFILE '.opencli\clis\fmhy'
$adapterFiles = @('utils.js', 'pages.js', 'page.js', 'crawl.js', 'search.js')

if ((Test-Path -LiteralPath $destination) -and -not $Force) {
    throw "FMHY adapter already exists at $destination. Re-run with -Force to replace the managed files."
}

New-Item -ItemType Directory -Path $destination -Force | Out-Null
foreach ($name in $adapterFiles) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination (Join-Path $destination $name) -Force
}

Write-Output "Installed FMHY OpenCLI adapter to $destination"
Write-Output 'Validate with: opencli validate fmhy'
