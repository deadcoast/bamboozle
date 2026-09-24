# Run from any working directory; file arguments remain relative to the caller.
$ErrorActionPreference = 'Stop'
$cliPath = Join-Path $PSScriptRoot 'cli.py'
$repoPython = Join-Path $PSScriptRoot '..\..\.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $repoPython -PathType Leaf) {
    & $repoPython $cliPath @args
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python $cliPath @args
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $cliPath @args
} else {
    Write-Error 'Python is unavailable. Set up the repository with uv sync --locked, then retry.'
    exit 1
}
exit $LASTEXITCODE
