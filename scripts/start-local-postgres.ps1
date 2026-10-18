param([int]$Port = 55438, [string]$PostgresBin = 'C:\Program Files\PostgreSQL\18\bin')
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$clusterPath = Join-Path $workspace 'instance\pg-test'
$logPath = Join-Path $workspace 'instance\pg-test.log'
$pgCtl = Join-Path $PostgresBin 'pg_ctl.exe'
$initdb = Join-Path $PostgresBin 'initdb.exe'
$createdb = Join-Path $PostgresBin 'createdb.exe'
if (-not (Test-Path -LiteralPath $pgCtl)) { throw "Set -PostgresBin to your PostgreSQL bin directory." }
New-Item -ItemType Directory -Force -Path (Join-Path $workspace 'instance') | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $clusterPath 'PG_VERSION'))) {
    & $initdb -D $clusterPath -U rag --auth=trust --encoding=UTF8 --locale=C
    if ($LASTEXITCODE -ne 0) { throw 'initdb failed' }
}
& $pgCtl -D $clusterPath status | Out-Null
if ($LASTEXITCODE -ne 0) {
    & $pgCtl -D $clusterPath -l $logPath -o "-p $Port -h 127.0.0.1" -w start
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL failed to start; inspect the log and port availability.' }
}
foreach ($database in @('rag', 'rag_test')) {
    $exists = & (Join-Path $PostgresBin 'psql.exe') -h 127.0.0.1 -p $Port -U rag -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$database'"
    if ($exists -ne '1') {
        & $createdb -h 127.0.0.1 -p $Port -U rag $database
        if ($LASTEXITCODE -ne 0) { throw "Could not create $database" }
    }
}
Write-Output "Workspace PostgreSQL is ready at 127.0.0.1:$Port. Local trust authentication; never expose this cluster."
