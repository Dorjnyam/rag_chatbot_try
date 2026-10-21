param([string]$PostgresBin = 'C:\Program Files\PostgreSQL\16\bin')
$ErrorActionPreference = 'Stop'
$workspace = Split-Path -Parent $PSScriptRoot
$clusterPath = Join-Path $workspace 'instance\pg-phase35'
$logPath = Join-Path $workspace 'instance\pg-phase35.log'
$port = 55439
$pgCtl = Join-Path $PostgresBin 'pg_ctl.exe'
$initdb = Join-Path $PostgresBin 'initdb.exe'
$psql = Join-Path $PostgresBin 'psql.exe'

foreach ($exe in @($pgCtl, $initdb, $psql)) {
    if (-not (Test-Path -LiteralPath $exe)) { throw "Required PostgreSQL executable is missing: $exe" }
}

$versionFile = Join-Path $clusterPath 'PG_VERSION'
if (Test-Path -LiteralPath $clusterPath) {
    if (-not (Test-Path -LiteralPath $versionFile)) {
        throw 'The designated Phase 3.5 data directory exists but has no PG_VERSION; refusing to initialize over it.'
    }
} else {
    New-Item -ItemType Directory -Path (Split-Path -Parent $clusterPath) -Force | Out-Null
    & $initdb -D $clusterPath -U postgres --auth=trust --encoding=UTF8 --locale=C
    if ($LASTEXITCODE -ne 0) { throw 'initdb failed; no existing database was modified.' }
}

& $pgCtl -D $clusterPath status | Out-Null
if ($LASTEXITCODE -ne 0) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    if ($listener) { throw "Port $port is already occupied; refusing to start the Phase 3.5 cluster." }
    & $pgCtl -D $clusterPath -l $logPath -o "-p $port -h 127.0.0.1" -w start
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL failed to start; inspect the private instance log.' }
}

$roleExists = & $psql -h 127.0.0.1 -p $port -U postgres -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='rag_phase35_test'"
if ($LASTEXITCODE -ne 0) { throw 'Could not verify the dedicated test role.' }
$roleExistsText = (@($roleExists) -join '').Trim()
if ($roleExistsText -ne '1') {
    & $psql -h 127.0.0.1 -p $port -U postgres -d postgres -c 'CREATE ROLE rag_phase35_test LOGIN CREATEDB NOSUPERUSER NOCREATEROLE'
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the restricted test role.' }
    & $psql -h 127.0.0.1 -p $port -U postgres -d postgres -c "COMMENT ON ROLE rag_phase35_test IS 'Disposable Phase 3.5 test cluster only'"
    if ($LASTEXITCODE -ne 0) { throw 'Could not mark the restricted test role.' }
}

$privileges = & $psql -h 127.0.0.1 -p $port -U postgres -d postgres -tAc "SELECT rolcreatedb || ',' || rolsuper || ',' || rolcreaterole FROM pg_roles WHERE rolname='rag_phase35_test'"
if ($LASTEXITCODE -ne 0 -or (@($privileges) -join '').Trim() -ne 'true,false,false') {
    throw 'The Phase 3.5 test role must have CREATEDB and must not have SUPERUSER or CREATEROLE.'
}
$identity = & $psql -h 127.0.0.1 -p $port -U postgres -d postgres -tAc "SHOW data_directory"
if ($LASTEXITCODE -ne 0 -or [System.IO.Path]::GetFullPath((@($identity) -join '').Trim()) -ne [System.IO.Path]::GetFullPath($clusterPath)) {
    throw 'The connected server data directory does not match the dedicated Phase 3.5 directory.'
}
$serverPort = & $psql -h 127.0.0.1 -p $port -U rag_phase35_test -d postgres -tAc "SHOW port"
$serverPortText = (@($serverPort) -join '').Trim()
if ($LASTEXITCODE -ne 0 -or $serverPortText -ne "$port") { throw 'The dedicated PostgreSQL server port did not verify.' }

$identityFile = Join-Path $clusterPath 'phase35-test-identity.txt'
$expectedIdentity = 'phase35-test:' + [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData(
    [Text.Encoding]::UTF8.GetBytes([System.IO.Path]::GetFullPath($clusterPath).ToLowerInvariant()))).ToLowerInvariant()
if (Test-Path -LiteralPath $identityFile) {
    $storedIdentity = [System.IO.File]::ReadAllText($identityFile).Trim()
    if ($storedIdentity -ne $expectedIdentity) { throw 'The private test cluster identity file does not match its data directory.' }
} else {
    [System.IO.File]::WriteAllText($identityFile, $expectedIdentity + "`n", [System.Text.UTF8Encoding]::new($false))
}
$observedIdentity = & $psql -h 127.0.0.1 -p $port -U postgres -d postgres -tAc "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname='postgres'"
$observedIdentityText = (@($observedIdentity) -join '').Trim()
if (-not $observedIdentityText) {
    & $psql -h 127.0.0.1 -p $port -U postgres -d postgres -c "COMMENT ON DATABASE postgres IS '$expectedIdentity'"
    if ($LASTEXITCODE -ne 0) { throw 'Could not mark the dedicated test cluster identity.' }
} elseif ($observedIdentityText -ne $expectedIdentity) {
    throw 'The control database has an unexpected comment; refusing to replace it.'
}
$userIdentity = & $psql -h 127.0.0.1 -p $port -U rag_phase35_test -d postgres -tAc "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=current_database()"
if ($LASTEXITCODE -ne 0 -or (@($userIdentity) -join '').Trim() -ne $expectedIdentity) {
    throw 'The restricted test role could not verify the dedicated cluster marker.'
}

Write-Output "Dedicated PostgreSQL test cluster verified at 127.0.0.1:$port (data directory: instance\pg-phase35)."
Write-Output 'The test role has CREATEDB only; the development cluster and production application role were not used.'
