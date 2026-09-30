# pcnotify uninstaller for Windows. No administrator, nothing machine-wide, no policy change.
# Same rules as install.ps1: no parameters, pure ASCII, PCNOTIFY_DRYRUN=1 prints the plan and
# changes nothing. It removes the package and the two shortcuts and keeps the config folder.

& {
    $ShortcutName = 'pcnotify.lnk'
    $ProbeSeconds = 15

    function Test-Flag([string]$Value) {
        return [bool]($Value -and $Value.Trim() -ne '' -and $Value.Trim() -ne '0')
    }

    $DryRun = Test-Flag $env:PCNOTIFY_DRYRUN
    $LocalAppData = $env:LOCALAPPDATA
    if (-not $LocalAppData) { $LocalAppData = [Environment]::GetFolderPath('LocalApplicationData') }
    $AppData = $env:APPDATA
    if (-not $AppData) { $AppData = [Environment]::GetFolderPath('ApplicationData') }

    function Say([string]$Text) { Write-Host $Text }
    function Plan([string]$Text) { Write-Host ('[plan] ' + $Text) }

    # The same probe as install.ps1: a candidate counts only when it prints a version of 3.10 or
    # newer and its own sys.executable.
    function Get-PythonInfo([string]$Exe, [string]$Pre) {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $Exe
        $psi.Arguments = ($Pre + ' -c "import sys;print(sys.version.split()[0]);print(sys.executable)"').Trim()
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        try { $process = [System.Diagnostics.Process]::Start($psi) } catch { return $null }
        $out = $process.StandardOutput.ReadToEndAsync()
        $null = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($ProbeSeconds * 1000)) {
            try { $process.Kill() } catch { }
            return $null
        }
        $lines = @($out.Result -split "`r?`n" | Where-Object { $_.Trim() -ne '' })
        if ($lines.Count -lt 2) { return $null }
        $match = [regex]::Match($lines[0].Trim(), '^(\d+)\.(\d+)')
        if (-not $match.Success) { return $null }
        $major = [int]$match.Groups[1].Value
        $minor = [int]$match.Groups[2].Value
        if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) { return $null }
        $path = $lines[1].Trim()
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
        return New-Object PSObject -Property @{ Exe = $path; Version = $lines[0].Trim() }
    }

    function Get-LocalPython {
        if (-not $LocalAppData) { return }
        $root = Join-Path $LocalAppData 'Programs\Python'
        if (-not (Test-Path -LiteralPath $root -PathType Container)) { return }
        Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^Python3(\d+)' } |
            Sort-Object -Descending -Property @{ Expression = { [int]([regex]::Match($_.Name, '^Python3(\d+)').Groups[1].Value) } } |
            ForEach-Object { Join-Path $_.FullName 'python.exe' } |
            Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
    }

    function Find-Python {
        foreach ($py in @(Get-Command py -CommandType Application -ErrorAction SilentlyContinue)) {
            $info = Get-PythonInfo $py.Path '-3'
            if ($info) { return $info }
        }
        foreach ($candidate in @(Get-Command python -CommandType Application -All -ErrorAction SilentlyContinue)) {
            $info = Get-PythonInfo $candidate.Path ''
            if ($info) { return $info }
        }
        foreach ($exe in @(Get-LocalPython)) {
            $info = Get-PythonInfo $exe ''
            if ($info) { return $info }
        }
        return $null
    }

    function Invoke-Uninstall {
        $code = 0
        Say 'pcnotify: desinstalador'
        if ($DryRun) { Say 'Modo de prueba (PCNOTIFY_DRYRUN): se muestra el plan y no se cambia nada.' }
        $info = Find-Python
        if (-not $info) {
            Say 'No se encontro Python 3.10 o mas nuevo, asi que no hay paquete que quitar con pip.'
        } else {
            Say ('Python: ' + $info.Exe + ' (' + $info.Version + ')')
            $pipArgs = @('-m', 'pip', 'uninstall', '-y', 'pcnotify')
            if ($DryRun) {
                Plan ($info.Exe + ' ' + ($pipArgs -join ' '))
            } else {
                & $info.Exe @pipArgs | Out-Host
                if ($LASTEXITCODE -ne 0) {
                    Say ('pip no pudo quitar pcnotify (codigo ' + $LASTEXITCODE + ').')
                    $code = 1
                }
            }
        }

        foreach ($folderName in @('Desktop', 'Programs')) {
            $folder = [Environment]::GetFolderPath($folderName)
            if (-not $folder) { continue }
            $link = Join-Path $folder $ShortcutName
            if (-not (Test-Path -LiteralPath $link -PathType Leaf)) {
                Say ('No hay acceso directo en ' + $link)
                continue
            }
            if ($DryRun) {
                Plan ('borrar: ' + $link)
                continue
            }
            try {
                Remove-Item -LiteralPath $link -Force -ErrorAction Stop
                Say ('Borrado: ' + $link)
            } catch {
                Say ('No se pudo borrar ' + $link + '. Borralo a mano.')
                $code = 1
            }
        }

        if ($AppData) {
            Say ('La configuracion queda en ' + (Join-Path $AppData 'pcnotify') + ' y no se borra; borrala a mano si ya no la quieres.')
        }
        return $code
    }

    $code = Invoke-Uninstall
    if ($PSCommandPath) { exit $code }
    $global:LASTEXITCODE = $code
}
