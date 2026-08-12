$cs = Get-CimInstance Win32_ComputerSystem
'Physical RAM total: {0:N1} GiB' -f ($cs.TotalPhysicalMemory/1GB)
Get-CimInstance Win32_PhysicalMemory | ForEach-Object { 'DIMM: {0:N0} GB ({1})' -f ($_.Capacity/1GB), $_.PartNumber }
$base = 'HKLM:\SYSTEM\ControlSet001\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
Get-ChildItem $base -ErrorAction SilentlyContinue | ForEach-Object {
  $p = Get-ItemProperty $_.PsPath -ErrorAction SilentlyContinue
  if ($p.'HardwareInformation.qwMemorySize') {
    'GPU: {0} | adapter mem: {1:N1} GiB' -f $p.DriverDesc, ($p.'HardwareInformation.qwMemorySize'/1GB)
  }
}
$os = Get-CimInstance Win32_OperatingSystem
'OS visible total: {0:N1} GiB | free: {1:N1} GiB' -f ($os.TotalVisibleMemorySize/1MB), ($os.FreePhysicalMemory/1MB)
