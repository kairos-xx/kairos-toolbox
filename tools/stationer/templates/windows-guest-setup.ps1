param(
    [Parameter(Mandatory = $true)]
    [string]$PublicKey
)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$administrator = [Security.Principal.WindowsBuiltInRole]::Administrator
if (-not $principal.IsInRole($administrator)) {
    throw 'Run this one-time guest setup from an elevated PowerShell session.'
}

$capability = Get-WindowsCapability -Online |
    Where-Object Name -Like 'OpenSSH.Server*' |
    Select-Object -First 1
if (-not $capability) {
    throw 'The Windows OpenSSH Server capability is unavailable.'
}
if ($capability.State -ne 'Installed') {
    Add-WindowsCapability -Online -Name $capability.Name | Out-Null
}

Set-Service -Name sshd -StartupType Automatic
Start-Service -Name sshd

$rule = Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' `
    -ErrorAction SilentlyContinue
if (-not $rule) {
    New-NetFirewallRule `
        -Name 'OpenSSH-Server-In-TCP' `
        -DisplayName 'OpenSSH Server (sshd)' `
        -Enabled True `
        -Direction Inbound `
        -Protocol TCP `
        -Action Allow `
        -LocalPort 22 | Out-Null
}

$keyPath = Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
$PublicKey.Trim() | Set-Content -LiteralPath $keyPath -Encoding ascii
& icacls.exe $keyPath /inheritance:r | Out-Null
& icacls.exe $keyPath /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null
Restart-Service -Name sshd

Write-Host 'Windows ARM build guest SSH setup is complete.'
