$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

Write-Host "============================================================"
Write-Host "X Following Hourly Runner"
Write-Host "Window: 19:00 - 06:00 (hourly on the hour)"
Write-Host ("Start time: {0}" -f (Get-Date))
Write-Host "============================================================"

while ($true) {
    $now = Get-Date
    $inWindow = ($now.Hour -ge 19) -or ($now.Hour -lt 6)

    if ($inWindow) {
        $next = $now.AddHours(1)
        $next = Get-Date -Date $next.Date -Hour $next.Hour -Minute 0 -Second 0
    } else {
        $next = Get-Date -Date $now.Date -Hour 19 -Minute 0 -Second 0
        if ($now.Hour -ge 19) {
            $next = $next.AddDays(1)
        }
    }

    $waitSeconds = [int]($next - $now).TotalSeconds
    Write-Host ("[{0}] Next run at {1} (in {2} seconds)..." -f $now, $next.ToString("yyyy-MM-dd HH:mm:ss"), $waitSeconds)
    Start-Sleep -Seconds $waitSeconds

    if ($inWindow) {
        Write-Host ("[{0}] Running get_x_following.py ..." -f (Get-Date))
        python get_x_following.py
    }
}

