Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. "$PSScriptRoot\..\..\scripts\lark-cli-bridge.ps1" `
    -LibraryOnly -Prefix "http://127.0.0.1:18765/" `
    -AllowedSheetTargets @("spreadsheet:sheet-id")

$script:columns = @("序号", "原问句", "运行ID")
$script:searchCount = 0
$script:tablePayload = $null

function New-CliResult {
    param([Parameter(Mandatory = $true)]$Payload)
    return [pscustomobject]@{
        returncode = 0
        stdout = $Payload | ConvertTo-Json -Depth 20 -Compress
        stderr = ""
    }
}

function Invoke-LocalCli {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $false)][string[]]$Arguments = @(),
        [Parameter(Mandatory = $false)][AllowEmptyString()][string]$StandardInput
    )
    if ($Name -ne "lark-cli") { throw "unexpected CLI: $Name" }
    switch ([string]$Arguments[1]) {
        "+workbook-info" {
            return New-CliResult @{ ok = $true; data = @{ sheets = @(@{
                sheet_id = "sheet-id"; sheet_name = "结果"; row_count = 278; column_count = 3
            }) } }
        }
        "+cells-get" {
            $range = [string]$Arguments[[Array]::IndexOf($Arguments, "--range") + 1]
            if ($range -eq "A1:C1") {
                $headerRow = @($script:columns | ForEach-Object { @{ value = $_ } })
                return New-CliResult @{ ok = $true; data = @{ ranges = @(@{ cells = @(,$headerRow) }) } }
            }
            if ($range -eq "A279:C279") {
                $verifiedRow = @(
                    @{ value = 277 },
                    @{ value = "测试问题" },
                    @{ value = "doubao-123" }
                )
                return New-CliResult @{
                    ok = $true
                    data = @{ ranges = @(@{ cells = @(,$verifiedRow) }) }
                }
            }
            return New-CliResult @{
                ok = $true
                data = @{ ranges = @(@{ cells = @(@(@{ value = 276 })) }) }
            }
        }
        "+cells-search" {
            $script:searchCount++
            if ($script:searchCount -eq 1) {
                return New-CliResult @{
                    ok = $true
                    data = @{ has_more = $false; next_offset = $null; matches = @() }
                }
            }
            return New-CliResult @{
                ok = $true
                data = @{
                    has_more = $false
                    next_offset = $null
                    matches = @(@{ address = "C279"; value = "doubao-123" })
                }
            }
        }
        "+table-put" {
            $script:tablePayload = $StandardInput | ConvertFrom-Json
            return New-CliResult @{ ok = $true; data = @{ written = 1 } }
        }
        default { throw "unexpected operation: $($Arguments[1])" }
    }
}

$request = @'
{"spreadsheet_token":"spreadsheet","sheet_id":"sheet-id","sheet_name":"结果","columns":["序号","原问句","运行ID"],"rows":[[null,"测试问题","doubao-123"]],"idempotency_column":"运行ID","sequence_column":"序号"}
'@ | ConvertFrom-Json
$receipt = Invoke-FeishuSheetAppend -Request $request

if ($receipt.ok -ne $true) { throw "append receipt was not successful" }
if ($receipt.data.appended_count -ne 1) { throw "append count mismatch" }
if ($receipt.data.verified_addresses[0] -ne "C279") { throw "verification address mismatch" }
if ($receipt.data.verified_ranges[0] -ne "A279:C279") { throw "full-row verification range mismatch" }
if ($script:tablePayload.sheets[0].data[0][0] -ne 277) { throw "sequence was not allocated" }
if ($script:tablePayload.sheets[0].data[0][2] -ne "doubao-123") {
    throw "idempotency value was not preserved"
}
if ($script:tablePayload.sheets[0].mode -ne "append") { throw "append mode was not used" }

$script:allowedSheetTargetSet.Clear()
$blocked = $false
try {
    [void](Invoke-FeishuSheetAppend -Request $request)
} catch {
    $blocked = $_.Exception.Message -match "no Feishu sheet writeback targets|not in FEISHU_SHEET_WRITEBACK_ALLOWED_TARGETS"
}
if (-not $blocked) { throw "unlisted sheet target was not rejected" }

"lark-cli bridge append assertions passed"
