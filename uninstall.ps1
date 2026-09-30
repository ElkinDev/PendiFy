# pcnotify uninstaller for Windows. No administrator, nothing machine-wide, no policy change.
# Same rules as install.ps1: no parameters, pure ASCII, PCNOTIFY_DRYRUN=1 prints the plan and
# changes nothing. It removes the package and the two shortcuts and keeps the config folder.

& {
    $ShortcutName = 'pcnotify.lnk'
    $ProbeSeconds = 15
    $PipSeconds = 120
    # Written by install.ps1: the interpreter pip installed into, one line.
    $RecordName = 'python.txt'

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
    function Note([string]$Text) { if ($DryRun) { Plan $Text } else { Say $Text } }

    # The same probe as install.ps1: a candidate counts only when it prints a version of 3.10 or
    # newer followed by its own sys.executable, and is not inside a virtual environment.
    function Get-PythonInfo([string]$Exe, [string]$Pre) {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $Exe
        $psi.Arguments = ($Pre + ' -c "import sys;print(sys.version.split()[0],sys.prefix==sys.base_prefix);print(sys.executable)"').Trim()
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
        $lines = @($out.Result -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
        for ($i = 0; $i -lt $lines.Count - 1; $i++) {
            $match = [regex]::Match($lines[$i], '^((\d+)\.(\d+)\S*)\s+(True|False)$')
            if (-not $match.Success) { continue }
            $path = $lines[$i + 1]
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { continue }
            $major = [int]$match.Groups[2].Value
            $minor = [int]$match.Groups[3].Value
            if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) { return $null }
            if ($match.Groups[4].Value -ne 'True') {
                Note ('se omite ' + $path + ': es un entorno virtual')
                return $null
            }
            return New-Object PSObject -Property @{ Exe = $path; Version = $match.Groups[1].Value }
        }
        return $null
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

    # True only when pip itself answers that the package is not found, so a Python that fails to
    # start or has no pip never reads as a removal.
    function Test-Gone([string]$Exe) {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $Exe
        $psi.Arguments = '-m pip show pcnotify'
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        try { $process = [System.Diagnostics.Process]::Start($psi) } catch { return $false }
        $out = $process.StandardOutput.ReadToEndAsync()
        $err = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($PipSeconds * 1000)) {
            try { $process.Kill() } catch { }
            return $false
        }
        return ($process.ExitCode -eq 1 -and ($out.Result + $err.Result) -match 'not found')
    }

    # The interpreter install.ps1 recorded, when the record names a file that still exists.
    function Get-RecordedPython([string]$RecordFile) {
        if (-not (Test-Path -LiteralPath $RecordFile -PathType Leaf)) { return $null }
        $recorded = ''
        try { $recorded = ([System.IO.File]::ReadAllText($RecordFile)).Trim() } catch { }
        if ($recorded -and (Test-Path -LiteralPath $recorded -PathType Leaf)) { return $recorded }
        Say ('El Python anotado en ' + $RecordFile + ' ya no existe; se busca otro.')
        return $null
    }

    function Invoke-Uninstall {
        $code = 0
        Say 'pcnotify: desinstalador'
        if ($DryRun) { Say 'Modo de prueba (PCNOTIFY_DRYRUN): se muestra el plan y no se cambia nada.' }
        $recordFile = Join-Path (Join-Path $AppData 'pcnotify') $RecordName
        $python = Get-RecordedPython $recordFile
        if ($python) {
            Say ('Python: ' + $python + ' (anotado en ' + $recordFile + ')')
        } else {
            $info = Find-Python
            if ($info) {
                Say ('Python: ' + $info.Exe + ' (' + $info.Version + ')')
                $python = $info.Exe
            }
        }
        if (-not $python) {
            Say 'No se encontro Python 3.10 o mas nuevo, asi que no hay paquete que quitar con pip.'
        } else {
            $pipArgs = @('-m', 'pip', 'uninstall', '-y', 'pcnotify')
            if ($DryRun) {
                Plan ($python + ' ' + ($pipArgs -join ' '))
                Plan ('comprobar: ' + $python + ' -m pip show pcnotify')
            } else {
                & $python @pipArgs | Out-Host
                if (-not (Test-Gone $python)) {
                    Say ('pcnotify sigue instalado en ' + $python + ' (pip uninstall, codigo ' + $LASTEXITCODE + '); no se borra nada mas.')
                    return 1
                }
                Say 'Paquete pcnotify quitado.'
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
