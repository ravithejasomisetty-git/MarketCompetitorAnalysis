# Convenience entry point for Windows PowerShell. The actual application is
# streamlit_research_app.py; this script selects its folder, ensures a local .env
# exists, then lets uv load dependencies and credentials before starting Streamlit.
# Stop on setup errors instead of continuing with an incomplete environment.
$ErrorActionPreference = 'Stop'
# Resolve paths relative to this launcher, regardless of the terminal's current folder.
$appDirectory = $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/'
}
Push-Location -LiteralPath $appDirectory
try {
    if (-not (Test-Path -LiteralPath '.env')) {
        Copy-Item -LiteralPath '.env.example' -Destination '.env'
        Write-Host 'Created .env. Fill in its keys for live research, or try offline demonstration.'
    }
    # Use the locked Python dependencies and load API keys from the local .env file.
    & uv run --locked --env-file .env python -m streamlit run streamlit_research_app.py
    if ($LASTEXITCODE -ne 0) { throw "App exited with code $LASTEXITCODE." }
} finally {
    # Always restore the terminal's original folder, even if startup fails.
    Pop-Location
}
