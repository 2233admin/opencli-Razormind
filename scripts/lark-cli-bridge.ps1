param(
    [int]$Port = 18765,
    [string]$Prefix = "http://+:18765/",
    [string]$EnvFile = "",
    [string[]]$AllowedSheetTargets = @(),
    [ValidateRange(1, 3600)][int]$ProcessTimeoutSeconds = 120,
    [switch]$LibraryOnly
)

$ErrorActionPreference = "Stop"
$script:processTimeoutSeconds = $ProcessTimeoutSeconds

$configuredEnvironment = @{}
if ($EnvFile) {
    if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
        throw "Bridge environment file was not found"
    }
    foreach ($line in Get-Content -LiteralPath $EnvFile) {
        if ($line -notmatch '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { continue }
        $configuredEnvironment[$Matches[1]] = $Matches[2].Trim().Trim('"')
    }
}
if (-not $env:LARK_CLI_BRIDGE_TOKEN) {
    $configuredToken = $configuredEnvironment["LARK_CLI_BRIDGE_TOKEN"]
    if (-not $configuredToken) { $configuredToken = $configuredEnvironment["API_AUTH_TOKEN"] }
    if ($configuredToken) { $env:LARK_CLI_BRIDGE_TOKEN = $configuredToken }
}

$allowedTargetSource = [string]($env:FEISHU_SHEET_WRITEBACK_ALLOWED_TARGETS ?? "")
if (-not $allowedTargetSource) {
    $allowedTargetSource = [string]($configuredEnvironment["FEISHU_SHEET_WRITEBACK_ALLOWED_TARGETS"] ?? "")
}
if ($AllowedSheetTargets.Count -eq 0 -and $allowedTargetSource) {
    $AllowedSheetTargets = @($allowedTargetSource -split '[,;]' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}
$script:allowedSheetTargetSet = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::Ordinal
)
foreach ($target in $AllowedSheetTargets) {
    if ($target) { [void]$script:allowedSheetTargetSet.Add([string]$target) }
}

# The default listener is reachable from Docker Desktop through
# host.docker.internal. A token is mandatory whenever the listener is not
# restricted to the local loopback interface.
$bridgeToken = [string]($env:LARK_CLI_BRIDGE_TOKEN ?? "")
if ($Prefix -notmatch "(?i)https?://(?:localhost|127\.0\.0\.1)(?::|/|$)" -and -not $bridgeToken) {
    throw "LARK_CLI_BRIDGE_TOKEN is required for a non-loopback bridge listener"
}

function Get-ObjectProperty {
    param(
        [Parameter(Mandatory = $false)]$Object,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if ($null -eq $Object) { return $null }
    if ($Object -is [System.Collections.IDictionary]) {
        return $Object[$Name]
    }
    $property = $Object.PSObject.Properties[$Name]
    return $(if ($null -eq $property) { $null } else { $property.Value })
}

function ConvertTo-ComparableCellText {
    param([Parameter(Mandatory = $false)]$Value)
    if ($null -eq $Value) { return "" }
    if ($Value -is [System.IFormattable]) {
        return $Value.ToString($null, [System.Globalization.CultureInfo]::InvariantCulture).Replace("`r`n", "`n")
    }
    return ([string]$Value).Replace("`r`n", "`n")
}

function Get-SheetWritebackMutexName {
    param([Parameter(Mandatory = $true)][string]$Target)
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Target)
    $digest = [System.Security.Cryptography.SHA256]::HashData($bytes)
    return "Local\OpenCLI-FeishuSheetWriteback-$([Convert]::ToHexString($digest).ToLowerInvariant())"
}

function Invoke-LocalCli {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $false)][string[]]$Arguments = @(),
        [Parameter(Mandatory = $false)][AllowEmptyString()][string]$StandardInput,
        [Parameter(Mandatory = $false)][ValidateRange(1, 3600)][int]$TimeoutSeconds = $script:processTimeoutSeconds
    )
    # Invoke the native npm shim directly. Running the PowerShell wrapper as a
    # child process can normalize escaped newlines inside JSON cell values into
    # literal newlines before the HTTP response is written.
    $command = (Get-Command "$Name.cmd" -CommandType Application -ErrorAction Stop).Source
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    if ($Name -eq "lark-cli") {
        # Avoid the PowerShell/cmd shim entirely: the shim's child-process
        # output is not stable for multiline Feishu cell values.
        $node = Get-Command node -CommandType Application -ErrorAction Stop |
            Where-Object { $_.Source -match '\\node\.exe$' } |
            Select-Object -First 1
        if (-not $node) { throw "node.exe was not found" }
        $startInfo.FileName = $node.Source
        $startInfo.ArgumentList.Add((Join-Path (Split-Path $command -Parent) "node_modules\@larksuite\cli\scripts\run.js"))
    } else {
        $startInfo.FileName = $command
    }
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    if ($PSBoundParameters.ContainsKey("StandardInput")) {
        $startInfo.RedirectStandardInput = $true
        $startInfo.StandardInputEncoding = [System.Text.UTF8Encoding]::new($false)
    }
    $startInfo.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $startInfo.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    foreach ($argument in $Arguments) { $startInfo.ArgumentList.Add([string]$argument) }
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        $process.Start() | Out-Null
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if ($startInfo.RedirectStandardInput) {
            $process.StandardInput.Write($StandardInput)
            $process.StandardInput.Close()
        }
        $timeoutMilliseconds = [int]($TimeoutSeconds * 1000)
        if (-not $process.WaitForExit($timeoutMilliseconds)) {
            try { $process.Kill($true) } catch { $process.Kill() }
            $process.WaitForExit()
            throw "$Name timed out after $TimeoutSeconds seconds"
        }
        # Complete redirected stream reads after process exit so full output is
        # retained without allowing pipe buffers to deadlock the child.
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        return [pscustomobject]@{
            returncode = $process.ExitCode
            stdout = $stdout
            stderr = $stderr
        }
    } finally {
        $process.Dispose()
    }
}

function ConvertFrom-LocalCliJson {
    param(
        [Parameter(Mandatory = $true)]$Result,
        [Parameter(Mandatory = $true)][string]$Operation
    )
    if ($Result.returncode -ne 0) {
        $detail = [string]$Result.stderr
        throw "$Operation failed with exit code $($Result.returncode): $($detail.Trim())"
    }
    try {
        $payload = [string]$Result.stdout | ConvertFrom-Json
    } catch {
        throw "$Operation returned invalid JSON"
    }
    if ((Get-ObjectProperty -Object $payload -Name "ok") -ne $true) {
        $errorPayload = Get-ObjectProperty -Object $payload -Name "error"
        $message = Get-ObjectProperty -Object $errorPayload -Name "message"
        if (-not $message) { $message = $errorPayload }
        if (-not $message) { $message = "$Operation was rejected" }
        throw $message
    }
    return $payload
}

function Get-SheetColumnLetter {
    param([Parameter(Mandatory = $true)][int]$Index)
    if ($Index -lt 0) { throw "column index must be non-negative" }
    $value = $Index + 1
    $letters = ""
    while ($value -gt 0) {
        $value--
        $letters = [char](65 + ($value % 26)) + $letters
        $value = [Math]::Floor($value / 26)
    }
    return $letters
}

function Find-SheetValues {
    param(
        [Parameter(Mandatory = $true)][string]$SpreadsheetToken,
        [Parameter(Mandatory = $true)][string]$SheetId,
        [Parameter(Mandatory = $true)][string]$Range,
        [Parameter(Mandatory = $true)][string]$Pattern,
        [Parameter(Mandatory = $true)][string]$Operation
    )
    $matches = @()
    $offset = 0
    do {
        $args = @(
            "sheets", "+cells-search", "--spreadsheet-token", $SpreadsheetToken,
            "--sheet-id", $SheetId, "--range", $Range, "--find", $Pattern,
            "--regex", "--match-entire-cell", "--offset", "$offset",
            "--as", "user", "--format", "json"
        )
        $page = ConvertFrom-LocalCliJson -Operation $Operation -Result (
            Invoke-LocalCli -Name "lark-cli" -Arguments $args
        )
        $matches += @($page.data.matches)
        $hasMore = $page.data.has_more -eq $true
        if ($hasMore) {
            $nextOffset = $page.data.next_offset
            if ($null -eq $nextOffset -or [int]$nextOffset -le $offset) {
                throw "$Operation returned an invalid pagination cursor"
            }
            $offset = [int]$nextOffset
        }
    } while ($hasMore)
    return $matches
}

function Invoke-FeishuSheetAppend {
    param([Parameter(Mandatory = $true)]$Request)

    $spreadsheetToken = [string]$Request.spreadsheet_token
    $requestedSheetId = [string]$Request.sheet_id
    $requestedSheetName = [string]$Request.sheet_name
    if (-not $spreadsheetToken -or (-not $requestedSheetId -and -not $requestedSheetName)) {
        throw "spreadsheet_token and sheet_id or sheet_name are required"
    }
    $columns = @($Request.columns | ForEach-Object { [string]$_ })
    $rows = @($Request.rows)
    if ($columns.Count -lt 1 -or $columns.Count -gt 100) {
        throw "columns must contain between 1 and 100 labels"
    }
    if (@($columns | Select-Object -Unique).Count -ne $columns.Count) {
        throw "columns must be unique"
    }
    if ($rows.Count -lt 1 -or $rows.Count -gt 200) {
        throw "rows must contain between 1 and 200 records"
    }
    foreach ($row in $rows) {
        if (@($row).Count -ne $columns.Count) {
            throw "every row must have the same number of cells as columns"
        }
    }

    $workbook = ConvertFrom-LocalCliJson -Operation "lark-cli workbook-info" -Result (
        Invoke-LocalCli -Name "lark-cli" -Arguments @(
            "sheets", "+workbook-info", "--spreadsheet-token", $spreadsheetToken,
            "--as", "user", "--format", "json"
        )
    )
    $sheet = @($workbook.data.sheets) | Where-Object {
        (-not $requestedSheetId -or [string]$_.sheet_id -eq $requestedSheetId) -and
        (-not $requestedSheetName -or [string]$_.sheet_name -eq $requestedSheetName)
    } | Select-Object -First 1
    if (-not $sheet) { throw "target sheet was not found" }
    $sheetId = [string]$sheet.sheet_id
    $sheetName = [string]$sheet.sheet_name
    $rowCount = [Math]::Max([int]$sheet.row_count, 1)
    if ([int]$sheet.column_count -lt $columns.Count) {
        throw "target sheet has fewer columns than the configured writeback schema"
    }

    $lastColumn = Get-SheetColumnLetter -Index ($columns.Count - 1)
    $header = ConvertFrom-LocalCliJson -Operation "lark-cli header read" -Result (
        Invoke-LocalCli -Name "lark-cli" -Arguments @(
            "sheets", "+cells-get", "--spreadsheet-token", $spreadsheetToken,
            "--sheet-id", $sheetId, "--range", "A1:${lastColumn}1",
            "--include", "value", "--as", "user", "--format", "json"
        )
    )
    $headerCells = @($header.data.ranges[0].cells[0])
    for ($index = 0; $index -lt $columns.Count; $index++) {
        if ([string]$headerCells[$index].value -ne $columns[$index]) {
            throw "target sheet header does not match configured column '$($columns[$index])'"
        }
    }

    $targetKey = "${spreadsheetToken}:${sheetId}"
    if ($script:allowedSheetTargetSet.Count -eq 0) {
        throw "no Feishu sheet writeback targets are configured"
    }
    if (-not $script:allowedSheetTargetSet.Contains($targetKey)) {
        throw "target sheet is not in FEISHU_SHEET_WRITEBACK_ALLOWED_TARGETS"
    }

    $targetMutex = [System.Threading.Mutex]::new(
        $false,
        (Get-SheetWritebackMutexName -Target $targetKey)
    )
    $lockTaken = $false
    try {
        try {
            $lockTaken = $targetMutex.WaitOne([TimeSpan]::FromSeconds(150))
        } catch [System.Threading.AbandonedMutexException] {
            $lockTaken = $true
        }
        if (-not $lockTaken) { throw "timed out waiting for the target sheet writeback lock" }

    $idempotencyColumn = [string]$Request.idempotency_column
    $idempotencyIndex = [Array]::IndexOf([object[]]$columns, $idempotencyColumn)
    if (-not $idempotencyColumn -or $idempotencyIndex -lt 0) {
        throw "idempotency_column must name one configured column"
    }
    $requestKeys = @($rows | ForEach-Object { [string](@($_)[$idempotencyIndex]) })
    if ($requestKeys.Count -ne @(
        $requestKeys | Where-Object { $_ } | Select-Object -Unique
    ).Count) {
        throw "every row requires a unique, non-empty idempotency value"
    }

    $idempotencyLetter = Get-SheetColumnLetter -Index $idempotencyIndex
    $escapedKeys = @($requestKeys | ForEach-Object { [Regex]::Escape($_) })
    $existingKeys = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    if ($rowCount -gt 1) {
        $pattern = "^(?:$($escapedKeys -join '|'))$"
        $found = @(Find-SheetValues -SpreadsheetToken $spreadsheetToken -SheetId $sheetId `
            -Range "${idempotencyLetter}2:${idempotencyLetter}${rowCount}" -Pattern $pattern `
            -Operation "lark-cli idempotency search"
        )
        foreach ($match in $found) {
            if ($match.value) { [void]$existingKeys.Add([string]$match.value) }
        }
    }
    $pendingRows = @()
    foreach ($row in $rows) {
        $materialized = @($row)
        if (-not $existingKeys.Contains([string]$materialized[$idempotencyIndex])) {
            $pendingRows += ,$materialized
        }
    }
    if ($pendingRows.Count -eq 0) {
        return @{
            ok = $true
            data = @{
                appended_count = 0
                skipped_count = $rows.Count
                verified_addresses = @()
                sheet_id = $sheetId
                sheet_name = $sheetName
            }
        }
    }

    $sequenceColumn = [string]$Request.sequence_column
    $sequenceIndex = [Array]::IndexOf([object[]]$columns, $sequenceColumn)
    if ($sequenceColumn -and $sequenceIndex -ge 0) {
        $sequenceLetter = Get-SheetColumnLetter -Index $sequenceIndex
        $maxSequence = 0
        if ($rowCount -gt 1) {
            $sequenceRead = ConvertFrom-LocalCliJson -Operation "lark-cli sequence read" -Result (
                Invoke-LocalCli -Name "lark-cli" -Arguments @(
                    "sheets", "+cells-get", "--spreadsheet-token", $spreadsheetToken,
                    "--sheet-id", $sheetId, "--range", "${sequenceLetter}2:${sequenceLetter}${rowCount}",
                    "--include", "value", "--as", "user", "--format", "json"
                )
            )
            foreach ($cellRow in @($sequenceRead.data.ranges[0].cells)) {
                $number = 0
                if ([int]::TryParse([string](@($cellRow)[0].value), [ref]$number)) {
                    $maxSequence = [Math]::Max($maxSequence, $number)
                }
            }
        }
        foreach ($row in $pendingRows) {
            if ($null -eq $row[$sequenceIndex] -or -not [string]$row[$sequenceIndex]) {
                $maxSequence++
                $row[$sequenceIndex] = $maxSequence
            }
        }
    }

    $dtypes = @{}
    foreach ($column in $columns) { $dtypes[$column] = "object" }
    foreach ($column in @($sequenceColumn, "关键词数", "参考资料数", "推荐追问数")) {
        if ($column -and $columns -contains $column) { $dtypes[$column] = "int64" }
    }
    $tablePayload = @{
        sheets = @(
            @{
                name = $sheetName
                mode = "append"
                header = $false
                allow_overwrite = $false
                columns = $columns
                data = $pendingRows
                dtypes = $dtypes
            }
        )
    } | ConvertTo-Json -Depth 20 -Compress
    [void](ConvertFrom-LocalCliJson -Operation "lark-cli table append" -Result (
        Invoke-LocalCli -Name "lark-cli" -Arguments @(
            "sheets", "+table-put", "--spreadsheet-token", $spreadsheetToken,
            "--sheets", "-", "--as", "user", "--format", "json"
        ) -StandardInput $tablePayload
    ))

    $pendingKeys = @($pendingRows | ForEach-Object { [string](@($_)[$idempotencyIndex]) })
    $verifyPattern = "^(?:$(@($pendingKeys | ForEach-Object { [Regex]::Escape($_) }) -join '|'))$"
    $matches = @(Find-SheetValues -SpreadsheetToken $spreadsheetToken -SheetId $sheetId `
        -Range "${idempotencyLetter}:${idempotencyLetter}" -Pattern $verifyPattern `
        -Operation "lark-cli append verification"
    )
    $expectedByKey = @{}
    foreach ($row in $pendingRows) {
        $expectedByKey[[string]$row[$idempotencyIndex]] = $row
    }
    $matchByKey = @{}
    foreach ($match in $matches) {
        $key = [string](Get-ObjectProperty -Object $match -Name "value")
        $address = [string](Get-ObjectProperty -Object $match -Name "address")
        if (-not $expectedByKey.ContainsKey($key)) { continue }
        if ($matchByKey.ContainsKey($key)) {
            throw "append verification found duplicate idempotency value '$key'"
        }
        if ($address -notmatch '(?i)(?:^|!)[A-Z]+([0-9]+)$') {
            throw "append verification returned invalid cell address '$address'"
        }
        $matchByKey[$key] = @{ address = $address; row = [int]$Matches[1] }
    }
    if ($matchByKey.Count -ne $pendingRows.Count) {
        throw "append verification found $($matchByKey.Count) of $($pendingRows.Count) unique idempotency values"
    }

    $verifiedAddresses = @()
    $verifiedRanges = @()
    foreach ($row in $pendingRows) {
        $key = [string]$row[$idempotencyIndex]
        $matched = $matchByKey[$key]
        $rowNumber = [int]$matched.row
        $rowRange = "A${rowNumber}:${lastColumn}${rowNumber}"
        $rowRead = ConvertFrom-LocalCliJson -Operation "lark-cli full-row verification" -Result (
            Invoke-LocalCli -Name "lark-cli" -Arguments @(
                "sheets", "+cells-get", "--spreadsheet-token", $spreadsheetToken,
                "--sheet-id", $sheetId, "--range", $rowRange,
                "--include", "value", "--as", "user", "--format", "json"
            )
        )
        $actualRows = @($rowRead.data.ranges[0].cells)
        if ($actualRows.Count -ne 1) {
            throw "full-row verification did not return exactly one row for '$key'"
        }
        $actualCells = @($actualRows[0])
        if ($actualCells.Count -lt $columns.Count) {
            throw "full-row verification returned too few cells for '$key'"
        }
        for ($index = 0; $index -lt $columns.Count; $index++) {
            $actualValue = Get-ObjectProperty -Object $actualCells[$index] -Name "value"
            if ((ConvertTo-ComparableCellText $actualValue) -cne (ConvertTo-ComparableCellText $row[$index])) {
                throw "full-row verification mismatch for '$key' in column '$($columns[$index])'"
            }
        }
        $verifiedAddresses += [string]$matched.address
        $verifiedRanges += $rowRange
    }
    return @{
        ok = $true
        data = @{
            appended_count = $pendingRows.Count
            skipped_count = $rows.Count - $pendingRows.Count
            verified_addresses = $verifiedAddresses
            verified_ranges = $verifiedRanges
            sheet_id = $sheetId
            sheet_name = $sheetName
        }
    }
    } finally {
        if ($lockTaken) { $targetMutex.ReleaseMutex() }
        $targetMutex.Dispose()
    }
}

if ($LibraryOnly) { return }

$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add($Prefix)
$listener.Start()

try {
    while ($listener.IsListening) {
        $context = $listener.GetContext()
        $response = $context.Response
        try {
            $path = $context.Request.Url.AbsolutePath
            if ($context.Request.HttpMethod -ne "POST" -or $path -notin @(
                "/feishu/records", "/feishu/sheets/append", "/doubao"
            )) {
                $response.StatusCode = 404
                $body = @{ error = "not_found" } | ConvertTo-Json -Compress
            } elseif ($bridgeToken -and
                $context.Request.Headers["X-Lark-CLI-Bridge-Token"] -ne $bridgeToken) {
                $response.StatusCode = 401
                $body = @{ error = "unauthorized" } | ConvertTo-Json -Compress
            } else {
                $reader = [System.IO.StreamReader]::new($context.Request.InputStream)
                try { $request = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
                if ($path -eq "/doubao") {
                    $command = [string]$request.command
                    if ($command -notin @("ask", "read", "status", "whoami")) {
                        throw "unsupported doubao command"
                    }
                    $args = @("doubao", $command)
                    if ($request.args) { $args += @($request.args | ForEach-Object { [string]$_ }) }
                    if ($command -eq "ask") {
                        $question = [string]$request.args[0]
                        if (-not $question) { throw "doubao ask requires a question" }
                        $open = Invoke-LocalCli -Name "opencli" -Arguments @(
                            "browser", "doubao-workflow", "open", "https://www.doubao.com/chat", "--window", "background"
                        )
                        if ($open.returncode -ne 0) { throw "could not open Doubao browser session" }
                        $wait = Invoke-LocalCli -Name "opencli" -Arguments @(
                            "browser", "doubao-workflow", "wait", "time", "3"
                        )
                        if ($wait.returncode -ne 0) { throw "Doubao browser session did not become ready" }
                        $fill = Invoke-LocalCli -Name "opencli" -Arguments @(
                            "browser", "doubao-workflow", "fill", '[contenteditable="true"]', $question
                        )
                        if ($fill.returncode -ne 0) { throw "could not fill the Doubao question" }
                        $send = Invoke-LocalCli -Name "opencli" -Arguments @(
                            "browser", "doubao-workflow", "click", "#flow-end-msg-send"
                        )
                        if ($send.returncode -ne 0) { throw "could not send the Doubao question" }
                        $settle = Invoke-LocalCli -Name "opencli" -Arguments @(
                            "browser", "doubao-workflow", "wait", "time", "25"
                        )
                        if ($settle.returncode -ne 0) { throw "Doubao response wait failed" }
                        $extract = Invoke-LocalCli -Name "opencli" -Arguments @(
                            "browser", "doubao-workflow", "extract", "--selector", "main", "--chunk-size", "20000"
                        )
                        if ($extract.returncode -ne 0) { throw "could not read the Doubao answer" }
                        $page = $extract.stdout | ConvertFrom-Json
                        $assistantText = [string]$page.content
                        if (-not $assistantText) { throw "Doubao returned no visible answer" }
                        $stdout = @(
                            @{ Role = "User"; Text = $question },
                            @{ Role = "Assistant"; Text = $assistantText },
                            @{ Role = "System"; Text = "Doubao browser conversation"; Url = [string]$page.url }
                        ) | ConvertTo-Json -Compress
                        $body = @{ returncode = 0; stdout = $stdout; stderr = "" } | ConvertTo-Json -Compress
                    } else {
                        $run = Invoke-LocalCli -Name "opencli" -Arguments $args
                        if ($run.returncode -ne 0) { throw "opencli doubao failed with exit code $($run.returncode)" }
                        $body = $run | ConvertTo-Json -Compress
                    }
                } elseif ($path -eq "/feishu/sheets/append") {
                    $body = Invoke-FeishuSheetAppend -Request $request | ConvertTo-Json -Depth 10 -Compress
                } else {
                    $appToken = [string]$request.app_token
                    $tableId = [string]$request.table_id
                    if (-not $appToken -or -not $tableId) { throw "app_token and table_id are required" }
                    $limit = [Math]::Min([Math]::Max([int]$request.limit, 1), 200)
                    $offset = [Math]::Max([int]$request.offset, 0)
                    $args = @(
                        "base", "+record-list", "--base-token", $appToken,
                        "--table-id", $tableId, "--limit", "$limit", "--format", "json"
                    )
                    if ($request.view_id) { $args += @("--view-id", [string]$request.view_id) }
                    if ($request.profile) { $args += @("--profile", [string]$request.profile) }
                    if ($offset -gt 0) { $args += @("--offset", "$offset") }
                    $run = Invoke-LocalCli -Name "lark-cli" -Arguments $args
                    if ($run.returncode -ne 0) {
                        throw "lark-cli failed with exit code $($run.returncode): $($run.stderr.ToString().Trim())"
                    }
                    $body = [string]$run.stdout
                }
            }
        } catch {
            $response.StatusCode = 500
            $body = @{ error = "bridge_error"; message = $_.Exception.Message } | ConvertTo-Json -Compress
        }
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
        $response.ContentType = "application/json; charset=utf-8"
        $response.ContentLength64 = $bytes.Length
        $response.OutputStream.Write($bytes, 0, $bytes.Length)
        $response.Close()
    }
} finally {
    $listener.Stop()
    $listener.Close()
}
