# Windows Cache and Temp Folder Analysis Script
# This script ONLY reports findings - it does NOT delete anything

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "Windows Temporary and Cache Folder Analysis" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

function Get-FolderSize {
    param (
        [string]$Path,
        [string]$Name,
        [string]$SafeToDelete,
        [string]$CleanCommand
    )

    if (Test-Path $Path) {
        try {
            $items = Get-ChildItem -Path $Path -Recurse -Force -ErrorAction SilentlyContinue
            $size = ($items | Measure-Object -Property Length -Sum -ErrorAction SilentlyContinue).Sum
            $fileCount = ($items | Where-Object { -not $_.PSIsContainer }).Count
            $folderCount = ($items | Where-Object { $_.PSIsContainer }).Count

            $sizeGB = [math]::Round($size / 1GB, 2)
            $sizeMB = [math]::Round($size / 1MB, 2)

            Write-Host "Location: $Name" -ForegroundColor Yellow
            Write-Host "Path: $Path"
            if ($sizeGB -gt 0.01) {
                Write-Host "Size: $sizeGB GB ($sizeMB MB)" -ForegroundColor Green
            } else {
                Write-Host "Size: $sizeMB MB" -ForegroundColor Green
            }
            Write-Host "Files: $fileCount | Folders: $folderCount"
            Write-Host "Safe to delete: $SafeToDelete" -ForegroundColor $(if ($SafeToDelete -eq "YES") { "Green" } elseif ($SafeToDelete -eq "CAUTION") { "Yellow" } else { "Red" })
            Write-Host "Clean command: $CleanCommand" -ForegroundColor Cyan
            Write-Host ""

            return @{
                Name = $Name
                Path = $Path
                SizeGB = $sizeGB
                SizeMB = $sizeMB
                FileCount = $fileCount
                SafeToDelete = $SafeToDelete
            }
        } catch {
            Write-Host "Location: $Name" -ForegroundColor Yellow
            Write-Host "Path: $Path"
            Write-Host "Error: Unable to access or calculate size" -ForegroundColor Red
            Write-Host "Error details: $($_.Exception.Message)"
            Write-Host ""
            return $null
        }
    } else {
        Write-Host "Location: $Name" -ForegroundColor Yellow
        Write-Host "Path: $Path"
        Write-Host "Status: Not found" -ForegroundColor Gray
        Write-Host ""
        return $null
    }
}

$results = @()

# 1. USER TEMP FOLDER
Write-Host "1. USER TEMPORARY FOLDER" -ForegroundColor Magenta
Write-Host "------------------------" -ForegroundColor Magenta
$userTemp = [Environment]::GetFolderPath("LocalApplicationData") + "\Temp"
$result = Get-FolderSize -Path $userTemp -Name "User Temp" `
    -SafeToDelete "YES" `
    -CleanCommand "Remove-Item -Path '$userTemp\*' -Recurse -Force"
$results += $result

# 2. WINDOWS TEMP FOLDER
Write-Host "2. WINDOWS TEMPORARY FOLDER" -ForegroundColor Magenta
Write-Host "---------------------------" -ForegroundColor Magenta
$winTemp = "C:\Windows\Temp"
$result = Get-FolderSize -Path $winTemp -Name "Windows Temp" `
    -SafeToDelete "CAUTION" `
    -CleanCommand "Remove-Item -Path '$winTemp\*' -Recurse -Force (requires admin)"
$results += $result

# 3. BROWSER CACHES
Write-Host "3. BROWSER CACHES" -ForegroundColor Magenta
Write-Host "-----------------" -ForegroundColor Magenta

# Chrome Cache
$chromePath = [Environment]::GetFolderPath("LocalApplicationData") + "\Google\Chrome\User Data\Default\Cache"
$result = Get-FolderSize -Path $chromePath -Name "Chrome Cache" `
    -SafeToDelete "YES" `
    -CleanCommand "Remove-Item -Path '$chromePath\*' -Recurse -Force (close Chrome first)"
$results += $result

# Chrome Code Cache
$chromeCodeCache = [Environment]::GetFolderPath("LocalApplicationData") + "\Google\Chrome\User Data\Default\Code Cache"
$result = Get-FolderSize -Path $chromeCodeCache -Name "Chrome Code Cache" `
    -SafeToDelete "YES" `
    -CleanCommand "Remove-Item -Path '$chromeCodeCache\*' -Recurse -Force (close Chrome first)"
$results += $result

# Edge Cache
$edgePath = [Environment]::GetFolderPath("LocalApplicationData") + "\Microsoft\Edge\User Data\Default\Cache"
$result = Get-FolderSize -Path $edgePath -Name "Edge Cache" `
    -SafeToDelete "YES" `
    -CleanCommand "Remove-Item -Path '$edgePath\*' -Recurse -Force (close Edge first)"
$results += $result

# Edge Code Cache
$edgeCodeCache = [Environment]::GetFolderPath("LocalApplicationData") + "\Microsoft\Edge\User Data\Default\Code Cache"
$result = Get-FolderSize -Path $edgeCodeCache -Name "Edge Code Cache" `
    -SafeToDelete "YES" `
    -CleanCommand "Remove-Item -Path '$edgeCodeCache\*' -Recurse -Force (close Edge first)"
$results += $result

# Firefox Cache
$firefoxPath = [Environment]::GetFolderPath("LocalApplicationData") + "\Mozilla\Firefox\Profiles"
if (Test-Path $firefoxPath) {
    $profiles = Get-ChildItem -Path $firefoxPath -Directory
    foreach ($profile in $profiles) {
        $cachePath = Join-Path $profile.FullName "cache2"
        $result = Get-FolderSize -Path $cachePath -Name "Firefox Cache ($($profile.Name))" `
            -SafeToDelete "YES" `
            -CleanCommand "Remove-Item -Path '$cachePath\*' -Recurse -Force (close Firefox first)"
        $results += $result
    }
}

# 4. NPM/PNPM/YARN CACHES
Write-Host "4. PACKAGE MANAGER CACHES" -ForegroundColor Magenta
Write-Host "-------------------------" -ForegroundColor Magenta

# NPM Cache
$npmCache = [Environment]::GetFolderPath("LocalApplicationData") + "\npm-cache"
$result = Get-FolderSize -Path $npmCache -Name "npm Cache" `
    -SafeToDelete "YES" `
    -CleanCommand "npm cache clean --force"
$results += $result

# PNPM Cache
$pnpmCache = [Environment]::GetFolderPath("LocalApplicationData") + "\pnpm\cache"
$result = Get-FolderSize -Path $pnpmCache -Name "pnpm Cache" `
    -SafeToDelete "YES" `
    -CleanCommand "pnpm store prune"
$results += $result

# Yarn Cache
$yarnCache = [Environment]::GetFolderPath("LocalApplicationData") + "\Yarn\Cache"
$result = Get-FolderSize -Path $yarnCache -Name "Yarn Cache" `
    -SafeToDelete "YES" `
    -CleanCommand "yarn cache clean"
$results += $result

# 5. WINDOWS UPDATE
Write-Host "5. WINDOWS UPDATE" -ForegroundColor Magenta
Write-Host "-----------------" -ForegroundColor Magenta

$updateCache = "C:\Windows\SoftwareDistribution\Download"
$result = Get-FolderSize -Path $updateCache -Name "Windows Update Download Cache" `
    -SafeToDelete "CAUTION" `
    -CleanCommand "Use Disk Cleanup (cleanmgr.exe) or Settings > System > Storage"
$results += $result

# 6. WINDOWS PREFETCH
Write-Host "6. WINDOWS PREFETCH" -ForegroundColor Magenta
Write-Host "-------------------" -ForegroundColor Magenta

$prefetch = "C:\Windows\Prefetch"
$result = Get-FolderSize -Path $prefetch -Name "Windows Prefetch" `
    -SafeToDelete "NO" `
    -CleanCommand "DO NOT DELETE - improves boot and app launch times"
$results += $result

# 7. RECYCLE BIN
Write-Host "7. RECYCLE BIN" -ForegroundColor Magenta
Write-Host "--------------" -ForegroundColor Magenta

Write-Host "Location: Recycle Bin" -ForegroundColor Yellow
Write-Host "Path: Various ($Recycle.Bin on each drive)"
try {
    $recycleBinSize = (New-Object -ComObject Shell.Application).NameSpace(0x0a).Items() | Measure-Object -Property Size -Sum
    $sizeGB = [math]::Round($recycleBinSize.Sum / 1GB, 2)
    $sizeMB = [math]::Round($recycleBinSize.Sum / 1MB, 2)
    if ($sizeGB -gt 0.01) {
        Write-Host "Size: $sizeGB GB ($sizeMB MB)" -ForegroundColor Green
    } else {
        Write-Host "Size: $sizeMB MB" -ForegroundColor Green
    }
} catch {
    Write-Host "Size: Unable to calculate" -ForegroundColor Gray
}
Write-Host "Safe to delete: YES" -ForegroundColor Green
Write-Host "Clean command: Right-click Recycle Bin > Empty Recycle Bin or Clear-RecycleBin -Force" -ForegroundColor Cyan
Write-Host ""

# 8. ADDITIONAL TEMP LOCATIONS
Write-Host "8. ADDITIONAL TEMP/CACHE LOCATIONS" -ForegroundColor Magenta
Write-Host "-----------------------------------" -ForegroundColor Magenta

# Windows Installer Cache (DO NOT DELETE)
$installerCache = "C:\Windows\Installer"
$result = Get-FolderSize -Path $installerCache -Name "Windows Installer Cache" `
    -SafeToDelete "NO" `
    -CleanCommand "DO NOT DELETE - needed for software updates and repairs"
$results += $result

# INetCache (IE/Edge legacy)
$inetCache = [Environment]::GetFolderPath("LocalApplicationData") + "\Microsoft\Windows\INetCache"
$result = Get-FolderSize -Path $inetCache -Name "INetCache (IE/Edge Legacy)" `
    -SafeToDelete "YES" `
    -CleanCommand "Remove-Item -Path '$inetCache\*' -Recurse -Force"
$results += $result

# Temp Internet Files
$tempInet = [Environment]::GetFolderPath("LocalApplicationData") + "\Microsoft\Windows\Temporary Internet Files"
$result = Get-FolderSize -Path $tempInet -Name "Temporary Internet Files" `
    -SafeToDelete "YES" `
    -CleanCommand "Remove-Item -Path '$tempInet\*' -Recurse -Force"
$results += $result

# SUMMARY
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "SUMMARY" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host ""

$totalSafe = ($results | Where-Object { $_.SafeToDelete -eq "YES" } | Measure-Object -Property SizeGB -Sum).Sum
$totalCaution = ($results | Where-Object { $_.SafeToDelete -eq "CAUTION" } | Measure-Object -Property SizeGB -Sum).Sum
$totalNo = ($results | Where-Object { $_.SafeToDelete -eq "NO" } | Measure-Object -Property SizeGB -Sum).Sum

Write-Host "Safe to delete (YES): $([math]::Round($totalSafe, 2)) GB" -ForegroundColor Green
Write-Host "Delete with caution (CAUTION): $([math]::Round($totalCaution, 2)) GB" -ForegroundColor Yellow
Write-Host "Do not delete (NO): $([math]::Round($totalNo, 2)) GB" -ForegroundColor Red
Write-Host ""
Write-Host "Total analyzed: $([math]::Round($totalSafe + $totalCaution + $totalNo, 2)) GB" -ForegroundColor Cyan
Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "Top 5 Largest Cleanup Candidates:" -ForegroundColor Yellow
Write-Host "================================================================" -ForegroundColor Cyan

$results | Where-Object { $_.SafeToDelete -eq "YES" } | Sort-Object -Property SizeGB -Descending | Select-Object -First 5 | ForEach-Object {
    Write-Host "$($_.Name): $($_.SizeGB) GB" -ForegroundColor Green
}

Write-Host ""
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "REMINDER: This script ONLY reports findings." -ForegroundColor Red
Write-Host "NO files were deleted. Use the provided commands to clean manually." -ForegroundColor Red
Write-Host "================================================================" -ForegroundColor Cyan
