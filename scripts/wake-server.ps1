# Rallume le serveur par Wake-on-LAN (paquet magique), depuis un PC du meme reseau local.
# Lit l'adresse MAC et l'adresse de broadcast dans ..\config.ini, section [wake].
# Usage : powershell -ExecutionPolicy Bypass -File scripts\wake-server.ps1
$ErrorActionPreference = 'Stop'
$config = Join-Path $PSScriptRoot '..\config.ini'
if (-not (Test-Path $config)) { throw "config.ini introuvable : copier config.example.ini" }

function Get-Setting($name) {
    $line = Get-Content $config | Where-Object { $_ -match "^\s*$name\s*=" } | Select-Object -First 1
    if ($line) { return ($line -replace "^\s*$name\s*=\s*", '').Trim() }
}

$mac = Get-Setting 'wake_mac'
$broadcast = Get-Setting 'wake_broadcast'
if (-not $broadcast) { $broadcast = '255.255.255.255' }
if ($mac -notmatch '^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$') { throw "wake_mac invalide ou absente dans config.ini ([wake])" }

# Paquet magique : 6 octets 0xFF, puis l'adresse MAC repetee 16 fois.
$macBytes = [byte[]]($mac -split '[:-]' | ForEach-Object { [Convert]::ToByte($_, 16) })
$packet = [byte[]](,0xFF * 6) + [byte[]]($macBytes * 16)

$udp = New-Object System.Net.Sockets.UdpClient
$udp.EnableBroadcast = $true
foreach ($port in 9, 7) { [void]$udp.Send($packet, $packet.Length, $broadcast, $port) }
$udp.Close()
Write-Host "Paquet magique envoye a $mac (broadcast $broadcast). Le serveur demarre en 1 a 2 minutes."
