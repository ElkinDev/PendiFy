# pcnotify installer for Windows. No administrator, nothing machine-wide, no policy change.
# It runs as a file and as text fetched from the repository and piped into PowerShell, so it takes
# no parameters; its three options are environment variables:
#   PCNOTIFY_SOURCE    what pip installs (default: the repository's main branch as a zip)
#   PCNOTIFY_DRYRUN=1  print every step as a [plan] line and change nothing
#   PCNOTIFY_NOSTART=1 leave the program stopped at the end
# The file is pure ASCII: Windows PowerShell 5.1 reads a file with no byte order mark in the
# system code page, so the messages are Spanish written without accented letters.

& {
    $Repo = 'ElkinDev/pcnotify'
    $DefaultSource = "https://github.com/$Repo/archive/refs/heads/main.zip"
    $PythonDownloads = 'https://www.python.org/downloads/'
    $WingetArgs = @('install', '--id', 'Python.Python.3.13', '-e', '--scope', 'user', '--silent',
        '--accept-package-agreements', '--accept-source-agreements')
    $WingetFolder = 'Python313'
    $ShortcutName = 'pcnotify.lnk'
    $StartArgs = '-m pcnotify'
    $ProbeSeconds = 15

    function Test-Flag([string]$Value) {
        return [bool]($Value -and $Value.Trim() -ne '' -and $Value.Trim() -ne '0')
    }

    $DryRun = Test-Flag $env:PCNOTIFY_DRYRUN
    $LocalAppData = $env:LOCALAPPDATA
    if (-not $LocalAppData) { $LocalAppData = [Environment]::GetFolderPath('LocalApplicationData') }

    function Say([string]$Text) { Write-Host $Text }
    function Plan([string]$Text) { Write-Host ('[plan] ' + $Text) }
    function Format-Arg([string]$Text) {
        if ($Text -match '\s') { return '"' + $Text + '"' }
        return $Text
    }

    # Runs one candidate and reads its version and its own sys.executable. A candidate that prints
    # nothing (the Microsoft Store alias stub in WindowsApps), fails to start, hangs past the probe
    # bound, or answers below 3.10 does not count.
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

    # %LOCALAPPDATA%\Programs\Python\Python3*\python.exe, newest minor version first.
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

    function Find-Python([bool]$UsePath) {
        if ($UsePath) {
            foreach ($py in @(Get-Command py -CommandType Application -ErrorAction SilentlyContinue)) {
                $info = Get-PythonInfo $py.Path '-3'
                if ($info) { return $info }
            }
            foreach ($candidate in @(Get-Command python -CommandType Application -All -ErrorAction SilentlyContinue)) {
                $info = Get-PythonInfo $candidate.Path ''
                if ($info) { return $info }
            }
        }
        foreach ($exe in @(Get-LocalPython)) {
            $info = Get-PythonInfo $exe ''
            if ($info) { return $info }
        }
        return $null
    }

    function Invoke-Install {
        Say 'pcnotify: instalador'
        if ($DryRun) { Say 'Modo de prueba (PCNOTIFY_DRYRUN): se muestra el plan y no se cambia nada.' }
        Say 'Buscando Python 3.10 o mas nuevo...'
        $info = Find-Python $true
        if ($info) {
            Say ('Python: ' + $info.Exe + ' (' + $info.Version + ')')
            $python = $info.Exe
        } else {
            $winget = @(Get-Command winget -CommandType Application -ErrorAction SilentlyContinue) | Select-Object -First 1
            if (-not $winget) {
                Say ('No se encontro Python 3.10 o mas nuevo. Instalalo desde ' + $PythonDownloads + ' y vuelve a pegar la linea de instalacion.')
                return 1
            }
            Say 'No hay Python. Se instala con winget, solo para este usuario.'
            if ($DryRun) {
                Plan ('winget ' + ($WingetArgs -join ' '))
                $python = Join-Path $LocalAppData ('Programs\Python\' + $WingetFolder + '\python.exe')
                Say ('Despues se busca Python de nuevo en su carpeta, por ejemplo ' + $python)
            } else {
                & $winget.Path @WingetArgs | Out-Host
                if ($LASTEXITCODE -ne 0) {
                    Say ('winget no pudo instalar Python (codigo ' + $LASTEXITCODE + '). Instalalo desde ' + $PythonDownloads)
                    return 1
                }
                $info = Find-Python $false
                if (-not $info) {
                    Say ('Python se instalo pero no aparece en ' + (Join-Path $LocalAppData 'Programs\Python') + '. Instalalo desde ' + $PythonDownloads)
                    return 1
                }
                Say ('Python: ' + $info.Exe + ' (' + $info.Version + ')')
                $python = $info.Exe
            }
        }

        $source = $env:PCNOTIFY_SOURCE
        if ($source) { $source = $source.Trim() }
        if (-not $source) { $source = $DefaultSource }
        $pipArgs = @('-m', 'pip', 'install', '--user', '--upgrade', '--no-warn-script-location', $source)
        Say 'Instalando pcnotify con pip...'
        if ($DryRun) {
            Plan ((Format-Arg $python) + ' ' + (($pipArgs | ForEach-Object { Format-Arg $_ }) -join ' '))
        } else {
            & $python @pipArgs | Out-Host
            if ($LASTEXITCODE -ne 0) {
                Say ('pip no pudo instalar pcnotify (codigo ' + $LASTEXITCODE + ').')
                return 1
            }
        }

        $pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
        if ((Test-Path -LiteralPath $python) -and -not (Test-Path -LiteralPath $pythonw)) { $pythonw = $python }
        $startLine = (Format-Arg $pythonw) + ' ' + $StartArgs

        Say 'Creando accesos directos...'
        foreach ($folderName in @('Desktop', 'Programs')) {
            $folder = [Environment]::GetFolderPath($folderName)
            if (-not $folder) {
                Say ('No hay carpeta ' + $folderName + ' para el acceso directo. Para iniciar: ' + $startLine)
                continue
            }
            $link = Join-Path $folder $ShortcutName
            if ($DryRun) {
                Plan ('acceso directo: ' + $link)
                continue
            }
            try {
                $shell = New-Object -ComObject WScript.Shell
                $shortcut = $shell.CreateShortcut($link)
                $shortcut.TargetPath = $pythonw
                $shortcut.Arguments = $StartArgs
                $shortcut.WorkingDirectory = $env:USERPROFILE
                $shortcut.Description = 'pcnotify'
                $shortcut.Save()
                Say ('Acceso directo: ' + $link)
            } catch {
                Say ('No se pudo crear el acceso directo ' + $link + '. Para iniciar: ' + $startLine)
            }
        }

        if (Test-Flag $env:PCNOTIFY_NOSTART) {
            Say ('Listo. Para iniciar: ' + $startLine)
            return 0
        }
        if ($DryRun) {
            Plan ('iniciar: ' + $startLine)
        } else {
            try {
                Start-Process -FilePath $pythonw -ArgumentList $StartArgs -ErrorAction Stop
            } catch {
                Say ('No se pudo iniciar pcnotify. Para iniciar: ' + $startLine)
                return 1
            }
        }
        Say 'El navegador mostrara un QR: escanealo con la camara del telefono.'
        return 0
    }

    $code = Invoke-Install
    # As a file the exit code is the process's; piped, exit would close the person's window, so the
    # code is left in LASTEXITCODE instead.
    if ($PSCommandPath) { exit $code }
    $global:LASTEXITCODE = $code
}
