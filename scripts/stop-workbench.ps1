$Port = 5173

$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($listener in $listeners) {
  try {
    $process = Get-Process -Id $listener.OwningProcess -ErrorAction Stop
    if ($process.ProcessName -match "python") {
      Stop-Process -Id $process.Id -Force
    }
  } catch {
  }
}

Add-Type -AssemblyName PresentationFramework
[System.Windows.MessageBox]::Show("AI Workbench has been stopped.", "AI Workbench")
