# 校园网自动认证系统 —— 代码签名脚本
#
# 用法：
#   # 1) 第一次：生成自签名证书、装进"当前用户/受信任的根证书"，并签名
#   powershell -ExecutionPolicy Bypass -File tools\sign.ps1 -SelfSigned -Trust
#
#   # 2) 以后只是重新签名（用已生成的证书）
#   powershell -ExecutionPolicy Bypass -File tools\sign.ps1
#
#   # 3) 买了正规代码签名证书后（证书装在 CurrentUser\My 或给 .pfx）
#   powershell -ExecutionPolicy Bypass -File tools\sign.ps1 -Pfx D:\codesign.pfx -PfxPassword 你的密码
#
# 说明：
#   - 自签名证书只能消除"当前这台机器/这个用户"的未知发布者提示
#   - 要让别人也不弹 SmartScreen，必须用受信任 CA 签发的证书（见 README）
#
param(
    [string[]] $Files = @(
        "stage_nuitka\校园网自动认证系统\校园网自动认证系统.exe",
        "installer\output\校园网自动认证系统_安装包_1.0.3.exe"
    ),
    [string] $Subject = "CN=CampusNet AutoLogin, O=Student Project, C=CN",
    [string] $Pfx,
    [string] $PfxPassword,
    [string] $TimestampUrl = "http://timestamp.digicert.com",
    [switch] $SelfSigned,   # 没有证书时自动生成自签名证书
    [switch] $Trust         # 把自签名证书装进当前用户的"受信任的根证书颁发机构"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

function Write-Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

# ---------------------------------------------------------------- 取证书
$cert = $null

if ($Pfx) {
    Write-Step "从 PFX 导入证书"
    if (-not (Test-Path $Pfx)) { throw "找不到 PFX：$Pfx" }
    $sec = ConvertTo-SecureString -String $PfxPassword -AsPlainText -Force
    $cert = Import-PfxCertificate -FilePath $Pfx -CertStoreLocation Cert:\CurrentUser\My `
        -Password $sec -Exportable
} else {
    $cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue |
        Where-Object { $_.Subject -eq $Subject } |
        Sort-Object NotAfter -Descending | Select-Object -First 1

    if (-not $cert -and $SelfSigned) {
        Write-Step "生成自签名代码签名证书"
        $cert = New-SelfSignedCertificate `
            -Type CodeSigningCert `
            -Subject $Subject `
            -KeyUsage DigitalSignature `
            -KeyAlgorithm RSA -KeyLength 3072 `
            -HashAlgorithm SHA256 `
            -CertStoreLocation Cert:\CurrentUser\My `
            -NotAfter (Get-Date).AddYears(3) `
            -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
        $cer = Join-Path $root "tools\CampusNet-自签名证书.cer"
        Export-Certificate -Cert $cert -FilePath $cer -Type CERT | Out-Null
        Write-Host "已导出公钥证书：$cer"
    }
}

if (-not $cert) {
    throw "没有可用证书。加 -SelfSigned 生成自签名证书，或用 -Pfx 指定证书文件。"
}

Write-Host "使用证书: $($cert.Subject)"
Write-Host "指纹    : $($cert.Thumbprint)"
Write-Host "有效期至: $($cert.NotAfter)"

# ---------------------------------------------------------------- 信任自签名证书
if ($Trust) {
    Write-Step "把证书加入『当前用户 → 受信任的根证书颁发机构』"
    try {
        $store = New-Object System.Security.Cryptography.X509Certificates.X509Store(
            "Root", [System.Security.Cryptography.X509Certificates.StoreLocation]::CurrentUser)
        $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadWrite)
        $store.Add($cert)
        $store.Close()
        Write-Host "已加入信任（只影响当前 Windows 用户）" -ForegroundColor Green
    } catch {
        Write-Warning "加入信任失败：$($_.Exception.Message)"
    }
}

# ---------------------------------------------------------------- 签名
$signed = 0
foreach ($f in $Files) {
    if (-not (Test-Path $f)) {
        Write-Warning "跳过（文件不存在）：$f"
        continue
    }
    Write-Step "签名 $f"
    try {
        $res = Set-AuthenticodeSignature -FilePath $f -Certificate $cert `
            -HashAlgorithm SHA256 -TimestampServer $TimestampUrl
        if ($res.Status -ne "Valid") {
            Write-Warning "带时间戳失败（$($res.StatusMessage)），改签不带时间戳"
            $res = Set-AuthenticodeSignature -FilePath $f -Certificate $cert -HashAlgorithm SHA256
        }
        $check = Get-AuthenticodeSignature $f
        $mark = if ($check.Status -eq "Valid") { "OK" } else { $check.Status }
        Write-Host ("  {0}  →  {1}  |  签名者: {2}" -f $mark, (Split-Path $f -Leaf),
            ($check.SignerCertificate.Subject)) -ForegroundColor $(if ($check.Status -eq "Valid") { "Green" } else { "Yellow" })
        $signed++
    } catch {
        Write-Warning "签名失败：$($_.Exception.Message)"
    }
}

Write-Step "完成，共处理 $signed 个文件"
Write-Host "提醒：" -ForegroundColor DarkGray
Write-Host "  - 自签名证书只对『装过这张证书的机器』有效" -ForegroundColor DarkGray
Write-Host "  - 别人的电脑仍会看到 SmartScreen 警告，除非购买受信任 CA 的代码签名证书" -ForegroundColor DarkGray
Write-Host "  - 详见 README『代码签名与 SmartScreen 警告』" -ForegroundColor DarkGray
