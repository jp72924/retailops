param(
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

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$startScript = Join-Path $scriptRoot "start-retailops.ps1"

$scriptParams = @{
    DbMode = "cloud"
    StorageMode = $StorageMode
    Port = $Port
    ProxyPort = $ProxyPort
    ProjectId = $ProjectId
    InstanceConnectionName = $InstanceConnectionName
    SecretName = $SecretName
    DbName = $DbName
    DbUser = $DbUser
    ProxyAddress = $ProxyAddress
    DjangoAddress = $DjangoAddress
    ProxyPath = $ProxyPath
    CredentialsFile = $CredentialsFile
    PythonCommand = $PythonCommand
    StartupTimeoutSeconds = $StartupTimeoutSeconds
    MediaProjectId = $MediaProjectId
    MediaPublicBucketName = $MediaPublicBucketName
    MediaPrivateBucketName = $MediaPrivateBucketName
    MediaCredentialsFile = $MediaCredentialsFile
    MediaSignerServiceAccount = $MediaSignerServiceAccount
    MediaPublicCustomEndpoint = $MediaPublicCustomEndpoint
    MediaRoot = $MediaRoot
    MediaUrl = $MediaUrl
    S3Provider = $S3Provider
    S3EndpointUrl = $S3EndpointUrl
    S3AccessKeyId = $S3AccessKeyId
    S3SecretAccessKey = $S3SecretAccessKey
    S3PublicBucketName = $S3PublicBucketName
    S3PrivateBucketName = $S3PrivateBucketName
    S3RegionName = $S3RegionName
}

if ($ApplyMigrations) {
    $scriptParams.ApplyMigrations = $true
}
if ($NoRunserver) {
    $scriptParams.NoRunserver = $true
}
if ($StopProxyOnExit) {
    $scriptParams.StopProxyOnExit = $true
}
if ($MediaUseIamSignBlob) {
    $scriptParams.MediaUseIamSignBlob = $true
}
if ($S3ProductPublic) {
    $scriptParams.S3ProductPublic = $true
}
if ($S3ReceiptSignedUrls) {
    $scriptParams.S3ReceiptSignedUrls = $true
}

& $startScript @scriptParams
exit $LASTEXITCODE
