try {
  Start-Process -FilePath "C:\Users\icefr\.local\bin\uv.exe" -ArgumentList "run","scripts/ele_worker.py" -WorkingDirectory "D:\script-workspace\imgbot" -Verb RunAs
  Write-Output "UAC-OK"
} catch {
  Write-Output ("UAC-FAIL: " + $_.Exception.Message)
}
