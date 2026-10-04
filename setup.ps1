# Narrator setup: creates a private Python environment in this folder, installs the
# voice engine, downloads the voice models, and adds a desktop shortcut.
param([switch]$NoShortcut)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = $PSScriptRoot

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Yellow }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)." } }

function Find-Python {
    foreach ($v in '3.12', '3.11') {
        try {
            $p = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $p) { return $p.Trim() }
        } catch {}
        $guess = Join-Path $env:LOCALAPPDATA ("Programs\Python\Python" + $v.Replace('.', '') + "\python.exe")
        if (Test-Path $guess) { return $guess }
    }
    return $null
}

try {
    Write-Host "Setting up Narrator. This downloads about 3 GB the first time and can take 10-20 minutes." -ForegroundColor Cyan

    Step "Looking for Python 3.12 or 3.11"
    $py = Find-Python
    if (-not $py) {
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            Write-Host "Python not found - installing Python 3.12 with winget..."
            winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
            $py = Find-Python
        }
        if (-not $py) { throw "Python 3.12 is required. Install it from https://www.python.org/downloads/ (check 'Add to PATH'), then run Setup.bat again." }
    }
    Write-Host "Using $py"

    Step "Creating the app environment"
    if (-not (Test-Path "$root\.venv\Scripts\python.exe")) { & $py -m venv "$root\.venv"; Check "Creating the environment" }
    $vpy = "$root\.venv\Scripts\python.exe"
    & $vpy -m pip install --quiet --upgrade pip; Check "Updating pip"

    Step "Installing the voice engine (PyTorch)"
    $hasNvidia = $null -ne (Get-Command nvidia-smi -ErrorAction SilentlyContinue)
    if ($hasNvidia) {
        Write-Host "NVIDIA graphics card found - installing the GPU version (fast)."
        & $vpy -m pip install torch --index-url https://download.pytorch.org/whl/cu128; Check "Installing PyTorch"
    } else {
        Write-Host "No NVIDIA graphics card - installing the CPU version (works, but slower)."
        & $vpy -m pip install torch --index-url https://download.pytorch.org/whl/cpu; Check "Installing PyTorch"
    }

    Step "Installing the rest"
    & $vpy -m pip install -r "$root\requirements.txt"; Check "Installing requirements"
    & $vpy -m spacy download en_core_web_sm; Check "Downloading the English language model"

    Step "Downloading the narrator voices"
    Push-Location $root
    & $vpy -c "import narrate; narrate.Narrator().speak('Hello.'); print('Voices ready.')"; Check "Downloading voices"
    Pop-Location

    if (-not $NoShortcut) {
    Step "Adding a desktop shortcut"
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Narrator.lnk'))
    $lnk.TargetPath = "$root\.venv\Scripts\pythonw.exe"
    $lnk.Arguments = "`"$root\app.py`""
    $lnk.WorkingDirectory = $root
    $lnk.IconLocation = "$root\narrator.ico"
    $lnk.Description = "Narrator - listen to your ebooks"
    $lnk.Save()
    }

    Write-Host "`nAll done! Open Narrator from the shortcut on your desktop." -ForegroundColor Green
} catch {
    Write-Host "`nSetup stopped: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Fix the problem above and run Setup.bat again - it picks up where it left off."
    exit 1
}
