# Downloads the UB-Mannheim build of Tesseract (the one the reader was tested with), installs it
# into a temporary folder, and copies what the app needs into the given destination:
#   tesseract.exe + its DLLs + tessdata\eng.traineddata
param([Parameter(Mandatory = $true)][string]$Dest)
$ErrorActionPreference = "Stop"

$headers = @{ "User-Agent" = "poe-harvest-build" }
if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "Bearer $env:GITHUB_TOKEN" }
$rel = Invoke-RestMethod -Headers $headers -Uri "https://api.github.com/repos/UB-Mannheim/tesseract/releases/latest"
$asset = $rel.assets | Where-Object { $_.name -match "w64-setup.*\.exe$" } | Select-Object -First 1
if (-not $asset) { throw "No 64-bit Tesseract installer in UB-Mannheim release $($rel.tag_name)" }
Write-Host "Tesseract $($rel.tag_name): $($asset.name)"
$setup = Join-Path $env:RUNNER_TEMP $asset.name
Invoke-WebRequest -Headers $headers -Uri $asset.browser_download_url -OutFile $setup

$tmp = Join-Path $env:RUNNER_TEMP "tesseract-install"
$p = Start-Process -FilePath $setup -ArgumentList "/S /D=$tmp" -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "Tesseract installer failed ($($p.ExitCode))" }

# The installer may ignore /D, or finish in the background: look where it went, for up to 2 minutes.
$candidates = @($tmp, "$env:ProgramFiles\Tesseract-OCR", "${env:ProgramFiles(x86)}\Tesseract-OCR",
                "$env:LOCALAPPDATA\Programs\Tesseract-OCR")
$found = $null
for ($i = 0; $i -lt 60 -and -not $found; $i++) {
    $found = $candidates | Where-Object { Test-Path (Join-Path $_ "tessdata\eng.traineddata") } | Select-Object -First 1
    if (-not $found) { Start-Sleep -Seconds 2 }
}
if (-not $found) {
    Get-ChildItem $env:RUNNER_TEMP | Format-Table Name
    throw "Tesseract wasn't installed in any of: $($candidates -join ', ')"
}
Write-Host "Tesseract installed in $found"
$tmp = $found

New-Item -ItemType Directory -Force -Path (Join-Path $Dest "tessdata") | Out-Null
Copy-Item (Join-Path $tmp "tesseract.exe") $Dest
Copy-Item (Join-Path $tmp "*.dll") $Dest
Copy-Item (Join-Path $tmp "tessdata\eng.traineddata") (Join-Path $Dest "tessdata")
foreach ($f in @("LICENSE", "README.md")) {
    if (Test-Path (Join-Path $tmp "doc\$f")) { Copy-Item (Join-Path $tmp "doc\$f") $Dest }
}
& (Join-Path $Dest "tesseract.exe") --version
Write-Host "Tesseract copied to $Dest"
