param(
    [ValidateSet("local", "postgres", "cloud")]
    [string]$DbMode = "local",
    [ValidateSet("local", "s3", "cloud")]
    [string]$StorageMode = "local",
    [int]$Port = 8000,
    [int]$ProxyPort = 5433,
    [switch]$ApplyMigrations,
    [switch]$NoRunserver,
    [switch]$StopProxyOnExit,
    [string]$ProjectId = "retailops-db-20260516",
    [string]$InstanceConnectionName = "retailops-db-20260516:northamerica-south1:retailops-postgres-01",
    [string]$SecretName = "retailops-db-password",
    [string]$DbName = "retailops",
    [string]$DbUser = "retailops_app",
    [string]$LocalDbName = "",
    [string]$PostgresHost = "127.0.0.1",
    [int]$PostgresPort = 5432,
    [string]$PostgresDbName = "retailops",
    [string]$PostgresUser = "retailops_app",
    [string]$PostgresPassword = "",
    [string]$PostgresSslMode = "disable",
    [string]$ProxyAddress = "127.0.0.1",
    [string]$DjangoAddress = "127.0.0.1",
    [string]$ProxyPath = "$env:LOCALAPPDATA\Programs\cloud-sql-proxy\cloud-sql-proxy.exe",
    [string]$CredentialsFile = "$env:APPDATA\RetailOps\cloudsql\retailops-cloudsql-client.json",
    [string]$PythonCommand = "python",
    [int]$StartupTimeoutSeconds = 30,
    [string]$MediaProjectId = "retailops-media",
    [string]$MediaPublicBucketName = "retailops-public-assets",
    [string]$MediaPrivateBucketName = "retailops-private-documents",
    [string]$MediaCredentialsFile = "$env:APPDATA\RetailOps\media\retailops-media-client.json",
    [switch]$MediaUseIamSignBlob,
    [string]$MediaSignerServiceAccount = "",
    [string]$MediaPublicCustomEndpoint = "",
    [string]$MediaRoot = "",
    [string]$MediaUrl = "",
    [ValidateSet("rustfs", "garage", "custom")]
    [string]$S3Provider = "rustfs",
    [string]$S3EndpointUrl = "",
    [string]$S3AccessKeyId = "",
    [string]$S3SecretAccessKey = "",
    [string]$S3PublicBucketName = "retailops-public-assets",
    [string]$S3PrivateBucketName = "retailops-private-documents",
    [string]$S3RegionName = "us-east-1",
    [switch]$S3ProductPublic,
    [switch]$S3ReceiptSignedUrls
)

$ErrorActionPreference = "Stop"

function Clear-ProcessEnv {
    param([string[]]$Names)

    foreach ($name in $Names) {
        Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
    }
}

function Clear-MediaGcsEnv {
    $names = @([System.Environment]::GetEnvironmentVariables("Process").Keys)
    foreach ($name in $names) {
        if ($name -like "MEDIA_GCS_*") {
            [System.Environment]::SetEnvironmentVariable($name, $null, "Process")
        }
    }
}

function Clear-MediaS3Env {
    $names = @([System.Environment]::GetEnvironmentVariables("Process").Keys)
    foreach ($name in $names) {
        if ($name -like "MEDIA_S3_*") {
            [System.Environment]::SetEnvironmentVariable($name, $null, "Process")
        }
    }
}

function Clear-MediaStorageEnv {
    Clear-MediaGcsEnv
    Clear-MediaS3Env
}

function Test-TcpOpen {
    param(
        [string]$Address,
        [int]$Port,
        [int]$TimeoutMilliseconds = 1000
    )

    $client = New-Object System.Net.Sockets.TcpClient
    $async = $null
    try {
        $async = $client.BeginConnect($Address, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMilliseconds, $false)) {
            return $false
        }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        if ($async -ne $null) {
            $async.AsyncWaitHandle.Close()
        }
        $client.Close()
    }
}

function Invoke-GcloudSecretAccess {
    param(
        [string]$ProjectId,
        [string]$SecretName
    )

    $command = "gcloud secrets versions access latest --secret=$SecretName --project=$ProjectId"
    $value = & cmd.exe /d /c $command 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($value)) {
        throw "Could not read Secret Manager secret '$SecretName' from project '$ProjectId'."
    }
    return ($value | Select-Object -First 1).Trim()
}

function Start-CloudSqlProxyIfNeeded {
    param(
        [string]$ProxyPath,
        [string]$CredentialsFile,
        [string]$ProxyAddress,
        [int]$ProxyPort,
        [string]$InstanceConnectionName,
        [string]$RepoRoot,
        [int]$StartupTimeoutSeconds
    )

    if (Test-TcpOpen -Address $ProxyAddress -Port $ProxyPort) {
        Write-Host "Cloud SQL Auth Proxy already listening on ${ProxyAddress}:${ProxyPort}."
        return [pscustomobject]@{ Started = $false; ProcessId = $null }
    }

    if (-not (Test-Path -LiteralPath $ProxyPath)) {
        throw "Cloud SQL Auth Proxy was not found at '$ProxyPath'. Install it before using cloud DB mode."
    }

    $logDir = Join-Path $RepoRoot "db_backups"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $stdoutLog = Join-Path $logDir "cloud-sql-proxy.stdout.log"
    $stderrLog = Join-Path $logDir "cloud-sql-proxy.stderr.log"

    $proxyArgs = @(
        "--address", $ProxyAddress,
        "--port", "$ProxyPort"
    )

    if (Test-Path -LiteralPath $CredentialsFile) {
        $proxyArgs += @("--credentials-file", $CredentialsFile)
    } else {
        Write-Host "Cloud SQL credentials file not found; proxy will use application default credentials."
    }

    $proxyArgs += $InstanceConnectionName

    Write-Host "Starting Cloud SQL Auth Proxy on ${ProxyAddress}:${ProxyPort}..."
    $process = Start-Process `
        -FilePath $ProxyPath `
        -ArgumentList $proxyArgs `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -PassThru

    for ($attempt = 1; $attempt -le $StartupTimeoutSeconds; $attempt++) {
        if (Test-TcpOpen -Address $ProxyAddress -Port $ProxyPort) {
            Write-Host "Cloud SQL Auth Proxy is ready. PID: $($process.Id)"
            return [pscustomobject]@{ Started = $true; ProcessId = $process.Id }
        }
        Start-Sleep -Seconds 1
    }

    $stderrTail = ""
    if (Test-Path -LiteralPath $stderrLog) {
        $stderrTail = (Get-Content -LiteralPath $stderrLog -Tail 20) -join [Environment]::NewLine
    }
    throw "Cloud SQL Auth Proxy did not become ready within $StartupTimeoutSeconds seconds. $stderrTail"
}

function Set-LocalDatabaseEnvironment {
    param([string]$LocalDbName)

    Clear-ProcessEnv -Names @(
        "DATABASE_URL",
        "DB_ENGINE",
        "DB_NAME",
        "DB_USER",
        "DB_PASSWORD",
        "DB_HOST",
        "DB_PORT",
        "DB_SSLMODE",
        "DB_CONN_MAX_AGE"
    )

    if (-not [string]::IsNullOrWhiteSpace($LocalDbName)) {
        $env:DB_ENGINE = "sqlite"
        $env:DB_NAME = $LocalDbName
        Write-Host "RetailOps database environment configured for local SQLite at '$LocalDbName'."
    } else {
        Write-Host "RetailOps database environment configured for default local SQLite."
    }
}

function Set-CloudDatabaseEnvironment {
    param(
        [string]$ProjectId,
        [string]$SecretName,
        [string]$DbName,
        [string]$DbUser,
        [string]$ProxyAddress,
        [int]$ProxyPort
    )

    Clear-ProcessEnv -Names @("DATABASE_URL")

    $dbPassword = Invoke-GcloudSecretAccess -ProjectId $ProjectId -SecretName $SecretName

    $env:DB_ENGINE = "postgres"
    $env:DB_NAME = $DbName
    $env:DB_USER = $DbUser
    $env:DB_PASSWORD = $dbPassword
    $env:DB_HOST = $ProxyAddress
    $env:DB_PORT = "$ProxyPort"
    $env:DB_SSLMODE = "disable"
    $env:DB_CONN_MAX_AGE = "60"

    Write-Host "RetailOps database environment configured for Cloud SQL."
}

function Set-PostgresDatabaseEnvironment {
    param(
        [string]$PostgresHost,
        [int]$PostgresPort,
        [string]$PostgresDbName,
        [string]$PostgresUser,
        [string]$PostgresPassword,
        [string]$PostgresSslMode
    )

    if ([string]::IsNullOrWhiteSpace($PostgresDbName) -or [string]::IsNullOrWhiteSpace($PostgresUser) -or [string]::IsNullOrWhiteSpace($PostgresHost)) {
        throw "PostgreSQL mode requires PostgresDbName, PostgresUser, and PostgresHost."
    }

    if ([string]::IsNullOrWhiteSpace($PostgresPassword)) {
        $PostgresPassword = $env:RETAILOPS_POSTGRES_PASSWORD
    }
    if ([string]::IsNullOrWhiteSpace($PostgresPassword)) {
        $PostgresPassword = $env:DB_PASSWORD
    }
    if ([string]::IsNullOrWhiteSpace($PostgresPassword)) {
        throw "PostgreSQL mode requires -PostgresPassword, RETAILOPS_POSTGRES_PASSWORD, or DB_PASSWORD."
    }

    Clear-ProcessEnv -Names @("DATABASE_URL")

    $env:DB_ENGINE = "postgres"
    $env:DB_NAME = $PostgresDbName
    $env:DB_USER = $PostgresUser
    $env:DB_PASSWORD = $PostgresPassword
    $env:DB_HOST = $PostgresHost
    $env:DB_PORT = "$PostgresPort"
    $env:DB_SSLMODE = $PostgresSslMode
    $env:DB_CONN_MAX_AGE = "60"

    Write-Host "RetailOps database environment configured for local PostgreSQL at ${PostgresHost}:${PostgresPort}."
}

function Set-LocalStorageEnvironment {
    param(
        [string]$MediaRoot,
        [string]$MediaUrl
    )

    Clear-ProcessEnv -Names @("MEDIA_STORAGE_BACKEND")
    Clear-MediaStorageEnv

    if (-not [string]::IsNullOrWhiteSpace($MediaRoot)) {
        $env:MEDIA_ROOT = $MediaRoot
    } else {
        Remove-Item -LiteralPath "Env:MEDIA_ROOT" -ErrorAction SilentlyContinue
    }

    if (-not [string]::IsNullOrWhiteSpace($MediaUrl)) {
        $env:MEDIA_URL = $MediaUrl
    } else {
        Remove-Item -LiteralPath "Env:MEDIA_URL" -ErrorAction SilentlyContinue
    }

    Write-Host "RetailOps media storage configured for local filesystem."
}

function Set-CloudStorageEnvironment {
    param(
        [string]$MediaProjectId,
        [string]$MediaPublicBucketName,
        [string]$MediaPrivateBucketName,
        [string]$MediaCredentialsFile,
        [bool]$MediaUseIamSignBlob,
        [string]$MediaSignerServiceAccount,
        [string]$MediaPublicCustomEndpoint
    )

    if ([string]::IsNullOrWhiteSpace($MediaPublicBucketName) -or [string]::IsNullOrWhiteSpace($MediaPrivateBucketName)) {
        throw "Cloud storage mode requires MediaPublicBucketName and MediaPrivateBucketName."
    }

    Clear-MediaStorageEnv
    Remove-Item -LiteralPath "Env:MEDIA_ROOT" -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath "Env:MEDIA_URL" -ErrorAction SilentlyContinue

    $env:MEDIA_STORAGE_BACKEND = "gcs"
    $env:MEDIA_GCS_PROJECT_ID = $MediaProjectId
    $env:MEDIA_GCS_PUBLIC_BUCKET_NAME = $MediaPublicBucketName
    $env:MEDIA_GCS_PRIVATE_BUCKET_NAME = $MediaPrivateBucketName
    $env:MEDIA_GCS_PRODUCT_PUBLIC = "true"
    $env:MEDIA_GCS_RECEIPT_SIGNED_URLS = "true"
    $env:MEDIA_GCS_SIGNED_URL_EXPIRATION = "900"

    if (-not [string]::IsNullOrWhiteSpace($MediaPublicCustomEndpoint)) {
        $env:MEDIA_GCS_PUBLIC_CUSTOM_ENDPOINT = $MediaPublicCustomEndpoint
    }

    if ($MediaUseIamSignBlob) {
        $env:MEDIA_GCS_IAM_SIGN_BLOB = "true"
    }

    if (-not [string]::IsNullOrWhiteSpace($MediaSignerServiceAccount)) {
        $env:MEDIA_GCS_SERVICE_ACCOUNT_EMAIL = $MediaSignerServiceAccount
    }

    if (-not [string]::IsNullOrWhiteSpace($MediaCredentialsFile) -and (Test-Path -LiteralPath $MediaCredentialsFile)) {
        $env:GOOGLE_APPLICATION_CREDENTIALS = $MediaCredentialsFile
        Write-Host "Google Cloud Storage credentials configured from '$MediaCredentialsFile'."
    } else {
        Write-Host "Media credentials file not found; Google Cloud Storage will use application default credentials."
    }

    Write-Host "RetailOps media storage configured for Google Cloud Storage."
}

function Resolve-S3EndpointUrl {
    param(
        [string]$S3Provider,
        [string]$S3EndpointUrl
    )

    if (-not [string]::IsNullOrWhiteSpace($S3EndpointUrl)) {
        return $S3EndpointUrl
    }

    if ($S3Provider -eq "rustfs") {
        return "http://127.0.0.1:9000"
    }
    if ($S3Provider -eq "garage") {
        return "http://127.0.0.1:3900"
    }

    throw "S3Provider=custom requires -S3EndpointUrl."
}

function Set-S3StorageEnvironment {
    param(
        [string]$S3Provider,
        [string]$S3EndpointUrl,
        [string]$S3AccessKeyId,
        [string]$S3SecretAccessKey,
        [string]$S3PublicBucketName,
        [string]$S3PrivateBucketName,
        [string]$S3RegionName,
        [bool]$S3ProductPublic,
        [bool]$S3ReceiptSignedUrls
    )

    if ([string]::IsNullOrWhiteSpace($S3AccessKeyId)) {
        $S3AccessKeyId = $env:RETAILOPS_S3_ACCESS_KEY_ID
    }
    if ([string]::IsNullOrWhiteSpace($S3AccessKeyId)) {
        $S3AccessKeyId = $env:MEDIA_S3_ACCESS_KEY_ID
    }
    if ([string]::IsNullOrWhiteSpace($S3SecretAccessKey)) {
        $S3SecretAccessKey = $env:RETAILOPS_S3_SECRET_ACCESS_KEY
    }
    if ([string]::IsNullOrWhiteSpace($S3SecretAccessKey)) {
        $S3SecretAccessKey = $env:MEDIA_S3_SECRET_ACCESS_KEY
    }

    if ([string]::IsNullOrWhiteSpace($S3AccessKeyId) -or [string]::IsNullOrWhiteSpace($S3SecretAccessKey)) {
        throw "S3 storage mode requires -S3AccessKeyId/-S3SecretAccessKey or RETAILOPS_S3_ACCESS_KEY_ID/RETAILOPS_S3_SECRET_ACCESS_KEY."
    }
    if ([string]::IsNullOrWhiteSpace($S3PublicBucketName) -or [string]::IsNullOrWhiteSpace($S3PrivateBucketName)) {
        throw "S3 storage mode requires S3PublicBucketName and S3PrivateBucketName."
    }

    Clear-MediaStorageEnv
    Remove-Item -LiteralPath "Env:MEDIA_ROOT" -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath "Env:MEDIA_URL" -ErrorAction SilentlyContinue

    $resolvedEndpoint = Resolve-S3EndpointUrl -S3Provider $S3Provider -S3EndpointUrl $S3EndpointUrl

    $env:MEDIA_STORAGE_BACKEND = "s3"
    $env:MEDIA_S3_ENDPOINT_URL = $resolvedEndpoint
    $env:MEDIA_S3_ACCESS_KEY_ID = $S3AccessKeyId
    $env:MEDIA_S3_SECRET_ACCESS_KEY = $S3SecretAccessKey
    $env:MEDIA_S3_REGION_NAME = $S3RegionName
    $env:MEDIA_S3_PUBLIC_BUCKET_NAME = $S3PublicBucketName
    $env:MEDIA_S3_PRIVATE_BUCKET_NAME = $S3PrivateBucketName
    $env:MEDIA_S3_PRODUCT_PUBLIC = $S3ProductPublic.ToString().ToLowerInvariant()
    $env:MEDIA_S3_RECEIPT_SIGNED_URLS = $S3ReceiptSignedUrls.ToString().ToLowerInvariant()
    $env:MEDIA_S3_SIGNED_URL_EXPIRATION = "900"
    $env:MEDIA_S3_ADDRESSING_STYLE = "path"
    $env:MEDIA_S3_SIGNATURE_VERSION = "s3v4"
    $env:MEDIA_S3_FILE_OVERWRITE = "false"
    $env:MEDIA_S3_PRODUCT_CACHE_CONTROL = "public, max-age=31536000, immutable"
    $env:MEDIA_S3_RECEIPT_CACHE_CONTROL = "private, max-age=0, no-store"

    Write-Host "RetailOps media storage configured for S3-compatible provider '$S3Provider' at '$resolvedEndpoint'."
}

function Invoke-ManagePy {
    param([string[]]$Arguments)

    & $PythonCommand manage.py @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "manage.py $($Arguments -join ' ') exited with code $LASTEXITCODE."
    }
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptRoot
Set-Location $repoRoot

$proxyInfo = [pscustomobject]@{ Started = $false; ProcessId = $null }

try {
    if ($DbMode -eq "cloud") {
        $proxyInfo = Start-CloudSqlProxyIfNeeded `
            -ProxyPath $ProxyPath `
            -CredentialsFile $CredentialsFile `
            -ProxyAddress $ProxyAddress `
            -ProxyPort $ProxyPort `
            -InstanceConnectionName $InstanceConnectionName `
            -RepoRoot $repoRoot `
            -StartupTimeoutSeconds $StartupTimeoutSeconds

        Set-CloudDatabaseEnvironment `
            -ProjectId $ProjectId `
            -SecretName $SecretName `
            -DbName $DbName `
            -DbUser $DbUser `
            -ProxyAddress $ProxyAddress `
            -ProxyPort $ProxyPort
    } elseif ($DbMode -eq "postgres") {
        Set-PostgresDatabaseEnvironment `
            -PostgresHost $PostgresHost `
            -PostgresPort $PostgresPort `
            -PostgresDbName $PostgresDbName `
            -PostgresUser $PostgresUser `
            -PostgresPassword $PostgresPassword `
            -PostgresSslMode $PostgresSslMode
    } else {
        Set-LocalDatabaseEnvironment -LocalDbName $LocalDbName
    }

    if ($StorageMode -eq "cloud") {
        Set-CloudStorageEnvironment `
            -MediaProjectId $MediaProjectId `
            -MediaPublicBucketName $MediaPublicBucketName `
            -MediaPrivateBucketName $MediaPrivateBucketName `
            -MediaCredentialsFile $MediaCredentialsFile `
            -MediaUseIamSignBlob:$MediaUseIamSignBlob.IsPresent `
            -MediaSignerServiceAccount $MediaSignerServiceAccount `
            -MediaPublicCustomEndpoint $MediaPublicCustomEndpoint
    } elseif ($StorageMode -eq "s3") {
        $productPublic = $true
        if ($PSBoundParameters.ContainsKey("S3ProductPublic")) {
            $productPublic = $S3ProductPublic.IsPresent
        }
        $receiptSignedUrls = $true
        if ($PSBoundParameters.ContainsKey("S3ReceiptSignedUrls")) {
            $receiptSignedUrls = $S3ReceiptSignedUrls.IsPresent
        }

        Set-S3StorageEnvironment `
            -S3Provider $S3Provider `
            -S3EndpointUrl $S3EndpointUrl `
            -S3AccessKeyId $S3AccessKeyId `
            -S3SecretAccessKey $S3SecretAccessKey `
            -S3PublicBucketName $S3PublicBucketName `
            -S3PrivateBucketName $S3PrivateBucketName `
            -S3RegionName $S3RegionName `
            -S3ProductPublic $productPublic `
            -S3ReceiptSignedUrls $receiptSignedUrls
    } else {
        Set-LocalStorageEnvironment -MediaRoot $MediaRoot -MediaUrl $MediaUrl
    }

    Write-Host "RetailOps startup profile: DB=$DbMode, storage=$StorageMode."

    if ($ApplyMigrations) {
        Write-Host "Applying migrations..."
        Invoke-ManagePy -Arguments @("migrate")
    } else {
        Write-Host "Checking migrations..."
        & $PythonCommand manage.py migrate --check
        if ($LASTEXITCODE -ne 0) {
            throw "Pending migrations detected or migration check failed. Re-run with -ApplyMigrations to apply pending migrations after confirming the selected DB profile is correct."
        }
    }

    Write-Host "Running Django system check..."
    Invoke-ManagePy -Arguments @("check")

    if ($NoRunserver) {
        Write-Host "Startup validation completed. Runserver was skipped because -NoRunserver was set."
        exit 0
    }

    Write-Host "Starting RetailOps at http://${DjangoAddress}:${Port}/ with DB=$DbMode and storage=$StorageMode..."
    Invoke-ManagePy -Arguments @("runserver", "${DjangoAddress}:${Port}")
} finally {
    if ($StopProxyOnExit -and $proxyInfo.Started -and $proxyInfo.ProcessId -ne $null) {
        Write-Host "Stopping Cloud SQL Auth Proxy started by this script..."
        Stop-Process -Id $proxyInfo.ProcessId -Force -ErrorAction SilentlyContinue
    }
}
