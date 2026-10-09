# Stop the local-docs-mcp / mcpo helper bridges without self-matching this process:
# split pattern strings so the literal never appears in any command line.
$p0 = 'helper' + '_server'  # legacy name
$p1 = 'local' + '_docs' + '_mcp'
$p2 = 'mcpo' + ' --port 80'
$p3 = '91'
Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like "*$p0*" -or $_.CommandLine -like "*$p1*" -or $_.CommandLine -like "*$p2$p3*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
if (Get-NetTCPConnection -LocalPort 8091 -State Listen -ErrorAction SilentlyContinue) {
    '8091 STILL LISTENING'
} else {
    '8091 free'
}

