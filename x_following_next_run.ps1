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

$wait = [int]($next - $now).TotalSeconds
$nextStr = $next.ToString('yyyy-MM-dd HH:mm:ss')

Write-Output ($inWindow.ToString() + ',' + $wait + ',' + $nextStr)

