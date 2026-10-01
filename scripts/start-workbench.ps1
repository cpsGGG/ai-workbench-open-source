$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Port = 5173
$Url = "http://127.0.0.1:$Port/"
$WindowIdentityScript = Join-Path (Split-Path -Parent $Root) "电脑管理\Set-AppWindowIdentity.ps1"
$WindowIcon = Join-Path $Root "assets\icons\ai-workbench-soft-v2.ico"
$Launcher = Join-Path $Root "打开AI工作台.cmd"

function Test-WorkbenchReady {
  try {
    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
    return $response.StatusCode -eq 200
  } catch {
    return $false
  }
}

function Get-ChromePath {
  $appPathKeys = @(
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe"
  )

  foreach ($appPathKey in $appPathKeys) {
    $chromePath = (Get-ItemProperty -Path $appPathKey -ErrorAction SilentlyContinue)."(default)"
    if ($chromePath -and (Test-Path -LiteralPath $chromePath)) {
      return $chromePath
    }
  }

  foreach ($chromePath in @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
  )) {
    if ($chromePath -and (Test-Path -LiteralPath $chromePath)) {
      return $chromePath
    }
  }

  throw "Google Chrome was not found."
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listener) {
  Start-Process -FilePath python -ArgumentList "server.py" -WorkingDirectory $Root -WindowStyle Hidden
}

$ready = $false
for ($i = 0; $i -lt 30; $i++) {
  if (Test-WorkbenchReady) {
    $ready = $true
    break
  }
  Start-Sleep -Milliseconds 500
}

if (-not $ready) {
  Add-Type -AssemblyName PresentationFramework
  [System.Windows.MessageBox]::Show("AI Workbench failed to start. Please check Python.", "AI Workbench")
  exit 1
}

$chromePath = Get-ChromePath
Start-Process -FilePath $chromePath -ArgumentList @(
  "--app=$Url",
  "--profile-directory=Default",
  "--no-first-run",
  "--no-default-browser-check"
)

if (Test-Path -LiteralPath $WindowIdentityScript) {
  $identityArguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-WindowStyle", "Hidden",
    "-File", ('"' + $WindowIdentityScript + '"'),
    "-TitlePattern", '"^AI 工作台$"',
    "-IconPath", ('"' + $WindowIcon + '"'),
    "-AppId", "Local.AIWorkbench",
    "-RelaunchCommand", ('"' + $Launcher + '"')
  )
  Start-Process -FilePath "powershell.exe" -ArgumentList $identityArguments -WindowStyle Hidden
}
