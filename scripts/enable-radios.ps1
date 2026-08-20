# Turns on the WiFi and Bluetooth software radios via the Windows Radio API.
# Run as the logged-in user (no admin needed). Exit code 0 = all radios on.
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskMethods = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name.StartsWith('IAsyncOperation')
}
$asTaskGeneric = $asTaskMethods[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

[Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType = WindowsRuntime] | Out-Null

$access = Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus])
Write-Output "RadioAccess: $access"

$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
foreach ($r in $radios) { Write-Output "Before: $($r.Name) [$($r.Kind)] = $($r.State)" }

$failed = $false
foreach ($r in $radios) {
    if ($r.State -ne [Windows.Devices.Radios.RadioState]::On) {
        $res = Await ($r.SetStateAsync([Windows.Devices.Radios.RadioState]::On)) ([Windows.Devices.Radios.RadioAccessStatus])
        Write-Output "SetState $($r.Kind): $res"
        if ("$res" -ne 'Allowed') { $failed = $true }
    }
}

Start-Sleep -Seconds 2
$radios2 = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
foreach ($r in $radios2) {
    Write-Output "After: $($r.Name) [$($r.Kind)] = $($r.State)"
    if ($r.State -ne [Windows.Devices.Radios.RadioState]::On) { $failed = $true }
}
if ($failed) { exit 1 } else { exit 0 }
