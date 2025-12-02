# Disk Analysis Script - DO NOT DELETE ANYTHING
# This script only reports disk usage

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "C: DRIVE DISK USAGE ANALYSIS" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

# Top 20 largest files
Write-Host "TOP 20 LARGEST FILES ON C: DRIVE" -ForegroundColor Yellow
Write-Host "Searching... (this may take several minutes)" -ForegroundColor Gray
try {
    $largestFiles = Get-ChildItem -Path C:\ -Recurse -File -ErrorAction SilentlyContinue |
        Sort-Object Length -Descending |
        Select-Object -First 20 FullName, @{Name='SizeGB';Expression={[math]::Round($_.Length/1GB,2)}}
    $largestFiles | Format-Table -AutoSize
} catch {
    Write-Host "Error scanning files: $_" -ForegroundColor Red
}

# Windows.old folder
Write-Host "`nWINDOWS.OLD FOLDER" -ForegroundColor Yellow
if (Test-Path 'C:\Windows.old') {
    try {
        $size = (Get-ChildItem -Path 'C:\Windows.old' -Recurse -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
        Write-Host "Size: $([math]::Round($size/1GB,2)) GB" -ForegroundColor Green
    } catch {
        Write-Host "Error scanning Windows.old: $_" -ForegroundColor Red
    }
} else {
    Write-Host "Not found" -ForegroundColor Gray
}

# Hibernation file
Write-Host "`nHIBERNATION FILE (hiberfil.sys)" -ForegroundColor Yellow
if (Test-Path 'C:\hiberfil.sys') {
    try {
        $file = Get-Item 'C:\hiberfil.sys' -Force
        Write-Host "Size: $([math]::Round($file.Length/1GB,2)) GB" -ForegroundColor Green
    } catch {
        Write-Host "Error accessing hiberfil.sys: $_" -ForegroundColor Red
    }
} else {
    Write-Host "Not found" -ForegroundColor Gray
}

# Page file
Write-Host "`nPAGE FILE (pagefile.sys)" -ForegroundColor Yellow
if (Test-Path 'C:\pagefile.sys') {
    try {
        $file = Get-Item 'C:\pagefile.sys' -Force
        Write-Host "Size: $([math]::Round($file.Length/1GB,2)) GB" -ForegroundColor Green
    } catch {
        Write-Host "Error accessing pagefile.sys: $_" -ForegroundColor Red
    }
} else {
    Write-Host "Not found" -ForegroundColor Gray
}

# Recycle Bin
Write-Host "`nRECYCLE BIN" -ForegroundColor Yellow
try {
    $recycleBin = (New-Object -ComObject Shell.Application).NameSpace(0xA)
    if ($recycleBin) {
        $size = ($recycleBin.Items() | Measure-Object -Property Size -Sum).Sum
        Write-Host "Size: $([math]::Round($size/1GB,2)) GB" -ForegroundColor Green
    } else {
        Write-Host "Cannot access Recycle Bin" -ForegroundColor Red
    }
} catch {
    Write-Host "Error accessing Recycle Bin: $_" -ForegroundColor Red
}

# Downloads folder
Write-Host "`nDOWNLOADS FOLDER" -ForegroundColor Yellow
try {
    $downloads = [Environment]::GetFolderPath('UserProfile') + '\Downloads'
    if (Test-Path $downloads) {
        $size = (Get-ChildItem -Path $downloads -Recurse -File -ErrorAction SilentlyContinue |
            Measure-Object -Property Length -Sum).Sum
        Write-Host "Path: $downloads" -ForegroundColor Gray
        Write-Host "Size: $([math]::Round($size/1GB,2)) GB" -ForegroundColor Green

        # Top 10 largest files in Downloads
        Write-Host "`nTop 10 largest files in Downloads:" -ForegroundColor Gray
        Get-ChildItem -Path $downloads -Recurse -File -ErrorAction SilentlyContinue |
            Sort-Object Length -Descending |
            Select-Object -First 10 Name, @{Name='SizeGB';Expression={[math]::Round($_.Length/1GB,2)}} |
            Format-Table -AutoSize
    } else {
        Write-Host "Not found" -ForegroundColor Gray
    }
} catch {
    Write-Host "Error accessing Downloads: $_" -ForegroundColor Red
}

# Large video/ISO files
Write-Host "`nLARGE VIDEO/ISO FILES (>1GB)" -ForegroundColor Yellow
Write-Host "Searching... (this may take several minutes)" -ForegroundColor Gray
try {
    $largeMedia = Get-ChildItem -Path C:\ -Recurse -File -Include *.mp4,*.mkv,*.avi,*.mov,*.iso,*.img -ErrorAction SilentlyContinue |
        Where-Object { $_.Length -gt 1GB } |
        Sort-Object Length -Descending |
        Select-Object FullName, @{Name='SizeGB';Expression={[math]::Round($_.Length/1GB,2)}}
    if ($largeMedia) {
        $largeMedia | Format-Table -AutoSize
    } else {
        Write-Host "No large video/ISO files found >1GB" -ForegroundColor Gray
    }
} catch {
    Write-Host "Error scanning for media files: $_" -ForegroundColor Red
}

# Temp folders
Write-Host "`nTEMP FOLDERS" -ForegroundColor Yellow
try {
    $tempPaths = @(
        "C:\Windows\Temp",
        "C:\Temp",
        "$env:USERPROFILE\AppData\Local\Temp"
    )

    foreach ($tempPath in $tempPaths) {
        if (Test-Path $tempPath) {
            $size = (Get-ChildItem -Path $tempPath -Recurse -File -ErrorAction SilentlyContinue |
                Measure-Object -Property Length -Sum).Sum
            Write-Host "$tempPath : $([math]::Round($size/1GB,2)) GB" -ForegroundColor Green
        }
    }
} catch {
    Write-Host "Error accessing temp folders: $_" -ForegroundColor Red
}

# Disk space summary
Write-Host "`nDISK SPACE SUMMARY" -ForegroundColor Yellow
Get-PSDrive C | Select-Object @{Name='Drive';Expression={$_.Name + ':'}},
    @{Name='Used (GB)';Expression={[math]::Round($_.Used/1GB,2)}},
    @{Name='Free (GB)';Expression={[math]::Round($_.Free/1GB,2)}},
    @{Name='Total (GB)';Expression={[math]::Round(($_.Used + $_.Free)/1GB,2)}},
    @{Name='Free %';Expression={[math]::Round($_.Free/($_.Used + $_.Free)*100,2)}} |
    Format-Table -AutoSize

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "ANALYSIS COMPLETE" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan
