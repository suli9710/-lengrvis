function Initialize-TrustedSigningModule {
    param([switch]$SkipModuleInstall)

    if (-not $SkipModuleInstall) {
        try {
            Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force -Scope CurrentUser | Out-Null
        }
        catch {
            Write-Host "NuGet package provider installation skipped or already satisfied: $($_.Exception.Message)"
        }
        Install-Module -Name TrustedSigning -MinimumVersion 0.5.0 -Force -Repository PSGallery -Scope CurrentUser
    }
}

function Require-AzureTrustedSigningEnv {
    $requiredEnv = @(
        "AZURE_TENANT_ID",
        "AZURE_CLIENT_ID",
        "AZURE_CLIENT_SECRET",
        "AZURE_TRUSTED_SIGNING_ENDPOINT",
        "AZURE_TRUSTED_SIGNING_ACCOUNT_NAME",
        "AZURE_TRUSTED_SIGNING_CERTIFICATE_PROFILE_NAME"
    )
    $missing = @()
    foreach ($name in $requiredEnv) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ([string]::IsNullOrWhiteSpace($value) -or $value.Trim().StartsWith("REPLACE_")) {
            $missing += $name
        }
    }
    if ($missing.Count -gt 0) {
        throw "Missing Azure Trusted Signing environment variables: $($missing -join ', ')"
    }
}

function Get-AzureTrustedSigningTimestampUrl {
    $timestampUrl = [Environment]::GetEnvironmentVariable("AZURE_TRUSTED_SIGNING_TIMESTAMP_RFC3161")
    if ([string]::IsNullOrWhiteSpace($timestampUrl)) {
        return "http://timestamp.acs.microsoft.com"
    }
    return $timestampUrl
}

function Invoke-TrustedWindowsSigning {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Files,
        [switch]$SkipModuleInstall
    )

    $isWindowsHost = $env:OS -eq "Windows_NT" -or [string][System.IO.Path]::DirectorySeparatorChar -eq "\"
    if (-not $isWindowsHost) {
        throw "Windows Authenticode signing must run on Windows."
    }

    Require-AzureTrustedSigningEnv
    Initialize-TrustedSigningModule -SkipModuleInstall:$SkipModuleInstall

    $timestampUrl = Get-AzureTrustedSigningTimestampUrl
    $targets = @()
    foreach ($file in $Files) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            throw "Missing executable to sign: $file"
        }
        $targets += (Resolve-Path -LiteralPath $file).Path
    }

    foreach ($target in $targets) {
        Write-Host "Signing with Azure Trusted Signing: $target"
        Invoke-TrustedSigning `
            -Endpoint $env:AZURE_TRUSTED_SIGNING_ENDPOINT `
            -CertificateProfileName $env:AZURE_TRUSTED_SIGNING_CERTIFICATE_PROFILE_NAME `
            -CodeSigningAccountName $env:AZURE_TRUSTED_SIGNING_ACCOUNT_NAME `
            -TimestampRfc3161 $timestampUrl `
            -TimestampDigest "SHA256" `
            -FileDigest "SHA256" `
            -Files $target

        $signature = Get-AuthenticodeSignature -LiteralPath $target
        if ($signature.Status -ne "Valid") {
            throw "Authenticode verification failed for $target : $($signature.Status)"
        }
        Write-Host "Authenticode signature verified: $target"
    }
}
