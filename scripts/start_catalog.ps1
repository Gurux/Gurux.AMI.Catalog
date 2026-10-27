param(
    [int]$Port = 8000,
    [string]$PythonExecutable
)
$ErrorActionPreference = 'Stop'
$catalogRoot = Split-Path -Parent $PSScriptRoot
$updaterProject = Join-Path (Split-Path -Parent $catalogRoot) 'Gurux.Updater.Net/Development/Gurux.Updater.Tool'
& dotnet build $updaterProject -c Release --verbosity quiet
if ($LASTEXITCODE -ne 0) { throw 'Updater build failed.' }
$env:GURUX_LOCALIZATION_VALIDATOR = Join-Path $updaterProject 'bin/Release/net10.0/Gurux.Updater.Tool.dll'
if (-not $PythonExecutable) {
    $pythonCommand = Get-Command python, py -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pythonCommand) { $PythonExecutable = $pythonCommand.Source }
    else {
        $pythonRoot = Join-Path $env:LOCALAPPDATA 'Programs/Python'
        $PythonExecutable = Get-ChildItem -LiteralPath $pythonRoot -Filter python.exe -Recurse |
            Select-Object -First 1 -ExpandProperty FullName
    }
}
if (-not $PythonExecutable) { throw 'Python not found. Specify -PythonExecutable.' }
& $PythonExecutable -B -u (Join-Path $PSScriptRoot 'serve_catalog.py') --port $Port `
    --sources (Join-Path $catalogRoot 'catalog-sources.json') --directory (Join-Path $catalogRoot '_site')
if ($LASTEXITCODE -ne 0) { throw 'Catalog server failed.' }
