# pcnotify

## Español

Un programa pequeño para Windows que avisa en tu teléfono cuando se encuentra tu partida y cuando empieza.
Se enlaza una sola vez con tu cuenta de Pendi leyendo un código QR, sin crear ninguna cuenta nueva.
Es gratis, no pide permisos de administrador y solo usa Python.

### Instalar

Abre PowerShell (menú Inicio, escribe PowerShell) y pega esta línea:

    irm https://raw.githubusercontent.com/ElkinDev/pcnotify/main/install.ps1 | iex

El instalador busca Python 3.10 o más nuevo; si no lo hay lo instala con winget solo para tu usuario, o te indica https://www.python.org/downloads/ cuando winget no existe. Después instala pcnotify con pip, deja un acceso directo `pcnotify` en el Escritorio y en el menú Inicio, y lo inicia.

Para ver qué haría sin cambiar nada: `$env:PCNOTIFY_DRYRUN = "1"` antes de pegar la línea. Para no iniciarlo al final: `$env:PCNOTIFY_NOSTART = "1"`.

### Enlazar

Al iniciarse, el navegador abre una página local con un código QR. Escanéalo con la cámara del teléfono: se abre la app Pendi y te pide confirmar el enlace. La página muestra cuando el enlace quedó hecho.

### Iniciar, detener, desinstalar

- Iniciar: el acceso directo `pcnotify`, o `pythonw -m pcnotify`, que es lo que ejecuta el acceso directo; sin consola, si ya está abierto o no puede iniciarse te lo dice en una ventana. Para verlo en una consola: `python -m pcnotify`.
- Detener: pulsa «Salir» en la página del programa; si no la tienes abierta, iniciarlo otra vez la abre. Como último recurso, cierra el proceso `pythonw.exe` en el Administrador de tareas.
- Desinstalar: pega en PowerShell `irm https://raw.githubusercontent.com/ElkinDev/pcnotify/main/uninstall.ps1 | iex`. Quita el paquete del mismo Python que lo instaló, comprueba que ya no está y quita los dos accesos directos; la configuración en `%APPDATA%\pcnotify` se queda y te dice dónde está.
- Actualizar: pulsa «Salir» en la página, desinstala con la línea de arriba y vuelve a pegar la línea de instalación; instala la última versión y conserva la configuración y el enlace. Pegar solo la línea de instalación no basta mientras la versión del paquete no cambie.

### Qué envía y a quién

Solo envía, al servicio de enlace de Pendi, el tipo de aviso (partida encontrada o partida empezada) junto con el identificador del enlace y el secreto de este PC, que viven en `%APPDATA%\pcnotify\config.json`. El servicio lo entrega a la cuenta que enlazaste. No envía tu nombre, tus partidas ni nada más del juego.

### `--dry`

Con `--dry` el programa solo avisa y no acepta la partida por ti.

Licencia: MIT

## English

A small Windows program that alerts your phone when your match is found and when it starts.
It links once to your Pendi account by scanning a QR code, with no new account of any kind.
It is free, needs no administrator rights and only uses Python.

### Install

Open PowerShell (Start menu, type PowerShell) and paste this line:

    irm https://raw.githubusercontent.com/ElkinDev/pcnotify/main/install.ps1 | iex

The installer looks for Python 3.10 or newer; when there is none it installs it with winget for your user only, or points you to https://www.python.org/downloads/ when winget is missing. Then it installs pcnotify with pip, leaves a `pcnotify` shortcut on the Desktop and in the Start menu, and starts it.

To see what it would do without changing anything, set `$env:PCNOTIFY_DRYRUN = "1"` before pasting the line. To leave it stopped at the end, set `$env:PCNOTIFY_NOSTART = "1"`.

### Link

When it starts, the browser opens a local page with a QR code. Scan it with the phone's camera: the Pendi app opens and asks you to confirm the link. The page shows when the link is done.

### Start, stop, uninstall

- Start: the `pcnotify` shortcut, or `pythonw -m pcnotify`, which is what the shortcut runs; with no console, it tells you in a window when it is already running or cannot start. To see it in a console: `python -m pcnotify`.
- Stop: press «Quit» on the program's page; if it is not open, starting the program again opens it. As a last resort, end the `pythonw.exe` process in Task Manager.
- Uninstall: paste `irm https://raw.githubusercontent.com/ElkinDev/pcnotify/main/uninstall.ps1 | iex` into PowerShell. It removes the package from the same Python that installed it, checks that it is gone and removes both shortcuts; the configuration in `%APPDATA%\pcnotify` stays, and it tells you where it is.
- Update: press «Quit» on the page, uninstall with the line above and paste the install line again; it installs the latest version and keeps the configuration and the link. Pasting the install line alone is not enough while the package's version does not change.

### What it sends and to whom

It only sends, to Pendi's link service, the alert kind (match found or match started) together with the link id and this PC's secret, which live in `%APPDATA%\pcnotify\config.json`. The service delivers it to the account you linked. It sends no name, no match history and nothing else from the game.

### `--dry`

With `--dry` the program only alerts and never accepts the match for you.

License: MIT

## Developer commands

    python -m pcnotify
    python -m pcnotify ping <kind>

The first loads the config, starts the page and opens it in the default browser while the PC is not paired.
It also watches the game client on this PC: when a match is found it accepts after a short random delay, beeps, and sends the found-match alert; when the loading screen starts it beeps and sends the started alert.
Add `--dry` to watch and alert without accepting. One copy runs per config folder: a second start opens the page of the one that runs and exits. Ctrl+C stops the page and the watcher together.
The second sends one alert with the stored pair and prints one line with the answer.

Both commands take `--data-dir <dir>`, used instead of `%APPDATA%` (the config file lives in a folder under it), and `--worker <address>`, used instead of the pairing service's address and accepted only as `http://127.0.0.1:<port>` or `http://localhost:<port>`, so a test run never reaches the real service. `--client-lockfile <file>` replaces the game client's lockfile, with no read of the running processes, and the client it names is reached over plain http on 127.0.0.1, so a test run never reaches a real client.

    python -m unittest discover -s tests -v
