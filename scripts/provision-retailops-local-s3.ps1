param(
    [ValidateSet("rustfs", "garage", "custom")]
    [string]$S3Provider = "rustfs",
    [string]$S3EndpointUrl = "",
    [string]$S3AccessKeyId = "",
    [string]$S3SecretAccessKey = "",
    [string]$S3PublicBucketName = "retailops-public-assets",
    [string]$S3PrivateBucketName = "retailops-private-documents",
    [string]$S3RegionName = "us-east-1",
    [bool]$S3ProductPublic = $true
)

$ErrorActionPreference = "Stop"

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

function Invoke-Aws {
    param([string[]]$Arguments)

    & aws @Arguments --no-cli-pager
    if ($LASTEXITCODE -ne 0) {
        throw "aws $($Arguments -join ' ') exited with code $LASTEXITCODE."
    }
}

function Ensure-Bucket {
    param(
        [string]$EndpointUrl,
        [string]$BucketName
    )

    & aws --endpoint-url $EndpointUrl s3api head-bucket --bucket $BucketName --no-cli-pager *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Bucket '$BucketName' already exists."
        return
    }

    Write-Host "Creating bucket '$BucketName'..."
    Invoke-Aws -Arguments @(
        "--endpoint-url", $EndpointUrl,
        "s3api", "create-bucket",
        "--bucket", $BucketName
    )
}

function Set-PublicReadPolicy {
    param(
        [string]$EndpointUrl,
        [string]$BucketName
    )

    $policy = @{
        Version = "2012-10-17"
        Statement = @(
            @{
                Sid = "RetailOpsPublicProductRead"
                Effect = "Allow"
                Principal = "*"
                Action = @("s3:GetObject")
                Resource = @("arn:aws:s3:::$BucketName/*")
            }
        )
    } | ConvertTo-Json -Depth 8

    $policyFile = Join-Path ([System.IO.Path]::GetTempPath()) "retailops-s3-public-policy-$BucketName.json"
    Set-Content -LiteralPath $policyFile -Value $policy -Encoding UTF8
    try {
        Write-Host "Applying public read policy to '$BucketName'..."
        Invoke-Aws -Arguments @(
            "--endpoint-url", $EndpointUrl,
            "s3api", "put-bucket-policy",
            "--bucket", $BucketName,
            "--policy", "file://$policyFile"
        )
    } finally {
        Remove-Item -LiteralPath $policyFile -Force -ErrorAction SilentlyContinue
    }
}

if (-not (Get-Command aws -ErrorAction SilentlyContinue)) {
    throw "AWS CLI was not found. Install AWS CLI before provisioning local S3 buckets."
}

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
    throw "Provisioning requires -S3AccessKeyId/-S3SecretAccessKey or RETAILOPS_S3_ACCESS_KEY_ID/RETAILOPS_S3_SECRET_ACCESS_KEY."
}
if ([string]::IsNullOrWhiteSpace($S3PublicBucketName) -or [string]::IsNullOrWhiteSpace($S3PrivateBucketName)) {
    throw "Provisioning requires S3PublicBucketName and S3PrivateBucketName."
}

$endpointUrl = Resolve-S3EndpointUrl -S3Provider $S3Provider -S3EndpointUrl $S3EndpointUrl

$previousAccessKey = $env:AWS_ACCESS_KEY_ID
$previousSecretKey = $env:AWS_SECRET_ACCESS_KEY
$previousRegion = $env:AWS_DEFAULT_REGION

try {
    $env:AWS_ACCESS_KEY_ID = $S3AccessKeyId
    $env:AWS_SECRET_ACCESS_KEY = $S3SecretAccessKey
    $env:AWS_DEFAULT_REGION = $S3RegionName

    Ensure-Bucket -EndpointUrl $endpointUrl -BucketName $S3PublicBucketName
    Ensure-Bucket -EndpointUrl $endpointUrl -BucketName $S3PrivateBucketName

    if ($S3ProductPublic) {
        Set-PublicReadPolicy -EndpointUrl $endpointUrl -BucketName $S3PublicBucketName
    } else {
        Write-Host "Skipping public product bucket policy because S3ProductPublic is false."
    }

    Write-Host "RetailOps local S3 buckets are ready at '$endpointUrl'."
} finally {
    $env:AWS_ACCESS_KEY_ID = $previousAccessKey
    $env:AWS_SECRET_ACCESS_KEY = $previousSecretKey
    $env:AWS_DEFAULT_REGION = $previousRegion
}
