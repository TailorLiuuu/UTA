$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$python=(Get-Content -Raw (Join-Path $PSScriptRoot 'python-path.txt')).Trim()
$cli=Join-Path $PSScriptRoot 'uta_windows.py'
$f=New-Object Windows.Forms.Form
$f.Icon=New-Object Drawing.Icon((Join-Path $PSScriptRoot 'UTA.ico'))
$f.Text='UTA - USB to Air';$f.Size=New-Object Drawing.Size(900,620);$f.StartPosition='CenterScreen'
$font=New-Object Drawing.Font('Segoe UI',10)
$f.Font=$font
$label=New-Object Windows.Forms.Label;$label.Text='Windows USB to Linux via SSH';$label.SetBounds(20,15,830,28);$f.Controls.Add($label)
$servers=New-Object Windows.Forms.ComboBox;$servers.DropDownStyle='DropDownList';$servers.SetBounds(20,55,400,28);$f.Controls.Add($servers)
$refresh=New-Object Windows.Forms.Button;$refresh.Text='Refresh';$refresh.SetBounds(440,53,100,30);$f.Controls.Add($refresh)
$check=New-Object Windows.Forms.Button;$check.Text='Check server';$check.SetBounds(555,53,140,30);$f.Controls.Add($check)
$add=New-Object Windows.Forms.Button;$add.Text='Add server';$add.SetBounds(710,53,140,30);$f.Controls.Add($add)
$grid=New-Object Windows.Forms.DataGridView;$grid.SetBounds(20,105,830,320);$grid.ReadOnly=$true;$grid.AllowUserToAddRows=$false;$grid.MultiSelect=$false;$grid.SelectionMode='FullRowSelect';$grid.AutoSizeColumnsMode='Fill'
foreach($name in @('Bus ID','Device','State','Remote ports')){[void]$grid.Columns.Add($name,$name)}
$f.Controls.Add($grid)
$connect=New-Object Windows.Forms.Button;$connect.Text='Connect';$connect.SetBounds(20,445,140,36);$f.Controls.Add($connect)
$disconnect=New-Object Windows.Forms.Button;$disconnect.Text='Disconnect';$disconnect.SetBounds(175,445,140,36);$f.Controls.Add($disconnect)
$cleanup=New-Object Windows.Forms.Button;$cleanup.Text='Retry cleanup';$cleanup.SetBounds(330,445,150,36);$f.Controls.Add($cleanup)
$status=New-Object Windows.Forms.TextBox;$status.Multiline=$true;$status.ReadOnly=$true;$status.ScrollBars='Vertical';$status.SetBounds(20,495,830,70);$f.Controls.Add($status)
$script:operation=$null;$script:serverIds=@();$script:lastRefresh=0
function Start-UTA([string[]]$Arguments){
 if($script:operation){return}
 foreach($s in $Arguments){if($s.Contains('"')){throw 'Double quotes are not allowed'}}
 $psi=New-Object Diagnostics.ProcessStartInfo;$psi.FileName=$python
 $psi.Arguments='"'+$cli+'" '+(($Arguments | ForEach-Object {'"'+$_+'"'}) -join ' ')
 $psi.UseShellExecute=$false;$psi.CreateNoWindow=$true;$psi.RedirectStandardOutput=$true;$psi.RedirectStandardError=$true
 $script:operation=[Diagnostics.Process]::Start($psi);$script:command=$Arguments[0]
 $status.Text='Working: '+$Arguments[0]
}
function Selected-Bus {if($grid.SelectedRows.Count -eq 1){return [string]$grid.SelectedRows[0].Cells[0].Value};throw 'Select a USB device first'}
function Selected-Server {if($servers.SelectedIndex -ge 0){return $script:serverIds[$servers.SelectedIndex]};throw 'Add and select a server first'}
function UI-Action([scriptblock]$Action){try{& $Action}catch{$status.Text=$_.Exception.Message}}
$refresh.Add_Click({Start-UTA @('status')})
$check.Add_Click({UI-Action {Start-UTA @('check','--server',(Selected-Server))}})
$connect.Add_Click({UI-Action {
 $bus=Selected-Bus
 if([Windows.Forms.MessageBox]::Show("Hand USB $bus to the selected Linux server? Close local serial/flash tools first.",'UTA','OKCancel') -eq 'OK'){Start-UTA @('connect','--device',$bus,'--server',(Selected-Server))}
}})
$disconnect.Add_Click({UI-Action {Start-UTA @('disconnect','--device',(Selected-Bus))}})
$cleanup.Add_Click({UI-Action {Start-UTA @('cleanup','--device',(Selected-Bus))}})
$add.Add_Click({
 $d=New-Object Windows.Forms.Form;$d.Text='Add SSH server';$d.Size=New-Object Drawing.Size(430,210);$d.StartPosition='CenterParent';$d.Font=$font
 $n=New-Object Windows.Forms.TextBox;$n.SetBounds(20,20,370,28);$n.Text='Server name';$d.Controls.Add($n)
 $t=New-Object Windows.Forms.TextBox;$t.SetBounds(20,60,370,28);$t.Text='SSH alias or user@host';$d.Controls.Add($t)
 $ok=New-Object Windows.Forms.Button;$ok.Text='Save';$ok.SetBounds(260,105,130,32);$ok.DialogResult='OK';$d.Controls.Add($ok);$d.AcceptButton=$ok
 if($d.ShowDialog($f) -eq 'OK'){Start-UTA @('server-add',$n.Text,$t.Text)};$d.Dispose()
})
$timer=New-Object Windows.Forms.Timer;$timer.Interval=500
$timer.Add_Tick({
 if($script:operation -and $script:operation.HasExited){
  $text=$script:operation.StandardOutput.ReadToEnd();$err=$script:operation.StandardError.ReadToEnd();$script:operation.Dispose();$script:operation=$null
  try{
   $v=$text | ConvertFrom-Json;if(-not $v.ok){throw $v.error}
   if($script:command -eq 'status'){
    $old=if($servers.SelectedIndex -ge 0){$script:serverIds[$servers.SelectedIndex]}else{''}
    $servers.Items.Clear();$script:serverIds=@()
    foreach($s in $v.result.servers.PSObject.Properties){$script:serverIds+= $s.Name;[void]$servers.Items.Add($s.Value.name)}
    $i=[Array]::IndexOf($script:serverIds,$old);if($i -ge 0){$servers.SelectedIndex=$i}elseif($servers.Items.Count){$servers.SelectedIndex=0}
    $selected=if($grid.SelectedRows.Count){[string]$grid.SelectedRows[0].Cells[0].Value}else{''}
    $grid.Rows.Clear()
    foreach($d in $v.result.devices){
     $s=$v.result.sessions | Where-Object busid -EQ $d.busid | Select-Object -First 1
     $state=if($s){$s.state}elseif($d.attached){'in use outside UTA'}elseif($d.shared){'shared outside UTA'}else{'local'}
     if($s.cleanup_pending -and $s.state -notin @('connecting','attached','enumerating')){$state+=' / cleanup pending'}
     $nodes=if($s){$s.nodes -join ', '}else{''}
     $i=$grid.Rows.Add($d.busid,$d.name,$state,$nodes);if($d.busid -eq $selected){$grid.Rows[$i].Selected=$true}
    }
    $errors=@($v.result.sessions | Where-Object {$_.error -or $_.cleanup_error -or $_.unbind_error} | ForEach-Object {"$($_.busid): $($_.error) $($_.cleanup_error) $($_.unbind_error)"})
    $status.Text=if($errors.Count){$errors -join "`r`n"}else{'Ready. USB is shared only after Connect. Closing this window keeps active sessions running.'}
   }else{$status.Text=($v.result | ConvertTo-Json -Depth 5)}
  }catch{$status.Text=($_.Exception.Message+' '+$err)}
  $script:lastRefresh=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
 }
 if(-not $script:operation -and ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()-$script:lastRefresh) -gt 5000){Start-UTA @('status')}
})
$f.Add_Shown({Start-UTA @('status');$timer.Start()})
[void]$f.ShowDialog();$timer.Stop();$timer.Dispose()
