param([string]$Python='',[string]$Destination="$env:LOCALAPPDATA\Programs\UTA")
$ErrorActionPreference='Stop'
if(-not $Python){
    $pythonCommand=Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if($pythonCommand){$Python=$pythonCommand.Source}
}
if(-not $Python -or -not (Test-Path $Python)){throw 'Python 3.9+ required. Use -Python C:\path\python.exe'}
& $Python -c 'import sys; sys.exit(sys.version_info < (3,9))'
if($LASTEXITCODE){throw 'Python 3.9+ required'}
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
foreach($file in @('uta_windows.py','uta_auth.py','UTA.ps1','UTA.ico','README.md')){Copy-Item (Join-Path $PSScriptRoot $file) (Join-Path $Destination $file) -Force}
$transfer=Join-Path $PSScriptRoot 'uta_transfer.py'
if(-not (Test-Path $transfer)){$transfer=Join-Path $PSScriptRoot '..\python\uta_transfer.py'}
Copy-Item $transfer (Join-Path $Destination 'uta_transfer.py') -Force
$port=Join-Path $PSScriptRoot 'uta_port.py'
if(-not (Test-Path $port)){$port=Join-Path $PSScriptRoot '..\python\uta_port.py'}
Copy-Item $port (Join-Path $Destination 'uta_port.py') -Force
foreach($doc in @('TRANSFERS.md','REMOTE-DEVICES.md')){
    $docPath=Join-Path $PSScriptRoot $doc
    if(-not (Test-Path $docPath)){$docPath=Join-Path (Join-Path $PSScriptRoot '..\docs') $doc}
    if(Test-Path $docPath){Copy-Item $docPath (Join-Path $Destination $doc) -Force}
}
[IO.File]::WriteAllText((Join-Path $Destination 'python-path.txt'),$Python,[Text.UTF8Encoding]::new($false))
[IO.File]::WriteAllText((Join-Path $Destination 'uta.cmd'),('@echo off'+"`r`n"+'"'+$Python+'" -B "%~dp0uta_windows.py" %*'+"`r`n"),[Text.Encoding]::ASCII)
[IO.File]::WriteAllText((Join-Path $Destination 'uta-server.cmd'),('@echo off'+"`r`n"+'"'+$Python+'" -B "%~dp0uta_transfer.py" %*'+"`r`n"),[Text.Encoding]::ASCII)
$shortcut=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'UTA.lnk'))
$shortcut.TargetPath="$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$shortcut.Arguments='-NoProfile -ExecutionPolicy Bypass -File "'+(Join-Path $Destination 'UTA.ps1')+'"'
$shortcut.WorkingDirectory=$Destination;$shortcut.Description='UTA Windows USB to Air';$shortcut.Save()
$shortcut.IconLocation=(Join-Path $Destination 'UTA.ico')+',0';$shortcut.Save()
# Request UAC for usbipd bind/unbind; do not change machine execution policy.
$shortcutPath=Join-Path ([Environment]::GetFolderPath('Desktop')) 'UTA.lnk'
$shortcutBytes=[IO.File]::ReadAllBytes($shortcutPath)
$shortcutBytes[21]=$shortcutBytes[21] -bor 0x20
[IO.File]::WriteAllBytes($shortcutPath,$shortcutBytes)
$programs=[Environment]::GetFolderPath('Programs')
Copy-Item $shortcutPath (Join-Path $programs 'UTA.lnk') -Force
Write-Output "Installed UTA: $Destination"
Write-Output 'Bind/unbind requires administrator rights: run UTA as administrator when sharing USB.'
