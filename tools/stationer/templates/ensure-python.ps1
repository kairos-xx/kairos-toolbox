# ensure-python.ps1 APPDIR - guarantee a Python >= @@WANT_MAJOR@@.@@WANT_MINOR@@ for district-pyz.cmd.
# Prefers an existing py/python interpreter; otherwise downloads the NuGet
# python package into APPDIR\python. Never aborts the installer.
# Rendered by tools.stationer.installer_support (@@WANT_MAJOR@@,
# @@WANT_MINOR@@, @@NUGET@@). No {work}-style braces may appear here.
param([string]$AppDir = (Split-Path -Parent $MyInvocation.MyCommand.Path))
$ErrorActionPreference = 'Continue'

function Test-Interpreter([string[]]$Argv) {
    try {
        & $Argv[0] @($Argv | Select-Object -Skip 1) -c "import sys; sys.exit(0 if sys.version_info[:2] >= (@@WANT_MAJOR@@, @@WANT_MINOR@@) else 1)" 2>$null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}

$Candidates = @(
    @('py', '-3'),
    @('python'),
    @('python3')
)
foreach ($Candidate in $Candidates) {
    if (Get-Command $Candidate[0] -ErrorAction SilentlyContinue) {
        if (Test-Interpreter $Candidate) {
            Write-Output "python: $($Candidate[0]) satisfies >=@@WANT_MAJOR@@.@@WANT_MINOR@@"
            exit 0
        }
    }
}

$Private = Join-Path $AppDir 'python\tools\python.exe'
if ((Test-Path $Private) -and (Test-Interpreter @($Private))) {
    Write-Output "python: $Private already present"
    exit 0
}

$Arch = (Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1).Architecture
$Package = if ($Arch -eq 12) { 'pythonarm64' } else { 'python' }
$Version = '@@NUGET@@'
$Zip = Join-Path $env:TEMP 'district-python.nupkg'
$Url = "https://api.nuget.org/v3-flatcontainer/$Package/$Version/$Package.$Version.nupkg"
Write-Output "python: downloading $Url"
try {
    if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
        & curl.exe -fsSL -o $Zip $Url
        if ($LASTEXITCODE -ne 0) { throw "curl exited $LASTEXITCODE" }
    } else {
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $Zip
    }
    $Target = Join-Path $AppDir 'python'
    Expand-Archive -Path $Zip -DestinationPath $Target -Force
    Remove-Item $Zip -Force -ErrorAction SilentlyContinue
    if (Test-Path $Private) {
        Write-Output "python: installed $Version into $Target"
    } else {
        Write-Output 'warning: python package had no tools\python.exe'
    }
} catch {
    Write-Output "warning: python bootstrap failed: $_"
}
exit 0
