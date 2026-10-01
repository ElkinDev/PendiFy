# pcnotify

## Español

Un programa pequeño para Windows que avisa en tu teléfono cuando se encuentra tu partida y cuando empieza.
Se enlaza una sola vez con tu cuenta de Pendi leyendo un código QR, sin crear ninguna cuenta nueva.
Es gratis, no pide permisos de administrador y solo usa Python.

### Instalar

Pulsa Windows + R, pega esta línea completa y pulsa Enter. También sirve en PowerShell o en el Símbolo del sistema.

    powershell -NoExit -NoProfile -ExecutionPolicy Bypass -Command "ri ~\pcnotify-install.ps1 -ea 0; irm https://raw.githubusercontent.com/ElkinDev/pcnotify/main/install.ps1 -OutFile ~\pcnotify-install.ps1; ~\pcnotify-install.ps1"

La línea guarda el instalador como `pcnotify-install.ps1` en tu carpeta de usuario y lo ejecuta desde ahí; puedes borrar ese archivo cuando termine.

La ventana queda abierta al terminar para que leas el resultado; ciérrala cuando acabe.

Si Windows dice que no puede acceder al archivo o el antivirus detiene la línea, no se instaló nada: actualiza las definiciones del antivirus y vuelve a intentarlo, o descarga `install.ps1` desde la página del repositorio, haz clic en él con el botón derecho y elige «Ejecutar con PowerShell».

Si ves "'irm' no se reconoce como un comando interno o externo", pegaste solo la parte corta en el Símbolo del sistema: usa la línea completa de arriba.

El instalador busca Python 3.10 o más nuevo; si no lo hay lo instala con winget solo para tu usuario, o te indica https://www.python.org/downloads/ cuando winget no existe. Después instala pcnotify con pip, deja un acceso directo `pcnotify` en el Escritorio y en el menú Inicio, y lo inicia.

En PowerShell: para ver qué haría sin cambiar nada: `$env:PCNOTIFY_DRYRUN = "1"` antes de pegar la línea. Para no iniciarlo al final: `$env:PCNOTIFY_NOSTART = "1"`.

### Enlazar

Al iniciarse, el navegador abre una página local con un código QR. Escanéalo con la cámara del teléfono: se abre la app Pendi y te pide confirmar el enlace. La página muestra cuando el enlace quedó hecho.

El código QR y la clave se muestran durante un minuto al pulsar «Mostrar el código» y luego se ocultan de nuevo.

El botón de arriba a la derecha cambia entre el tema claro y el oscuro, y el programa guarda la elección para la próxima vez.

### Iniciar, detener, desinstalar

- Iniciar: el acceso directo `pcnotify`, o `pythonw -m pcnotify`, que es lo que ejecuta el acceso directo; sin consola, si ya está abierto o no puede iniciarse te lo dice en una ventana. Para verlo en una consola: `python -m pcnotify`.
- Detener: pulsa «Salir» en la página del programa; si no la tienes abierta, iniciarlo otra vez la abre. Como último recurso, cierra el proceso `pythonw.exe` en el Administrador de tareas.
- Pausar: «Pausar avisos», en la tarjeta «Este PC» de la página, deja de leer el cliente del juego: no acepta partidas ni avisa a tu teléfono, y la página sigue abierta. «Reanudar avisos» vuelve a leerlo. La pausa no se guarda: cada vez que el programa se inicia, empieza activo.
- Iniciar con Windows: el interruptor «Iniciar con Windows», en la misma tarjeta, está apagado hasta que lo enciendas. Encendido, el programa empieza solo al iniciar sesión en Windows, solo para tu usuario y sin abrir el navegador ni la página.
- Actividad: la tarjeta «Actividad» de la página lista lo que el programa hizo desde que empezó: el cliente del juego encontrado y perdido, cada fase, la partida aceptada y el aviso enviado. Guarda las últimas 50 líneas, solo en memoria.
- Desinstalar: pega `powershell -NoExit -NoProfile -ExecutionPolicy Bypass -Command "ri ~\pcnotify-uninstall.ps1 -ea 0; irm https://raw.githubusercontent.com/ElkinDev/pcnotify/main/uninstall.ps1 -OutFile ~\pcnotify-uninstall.ps1; ~\pcnotify-uninstall.ps1"` igual que la línea de instalación; deja `pcnotify-uninstall.ps1` en tu carpeta de usuario, que puedes borrar. Quita el paquete del mismo Python que lo instaló, comprueba que ya no está, quita los dos accesos directos y quita el inicio con Windows si estaba encendido; la configuración en `%APPDATA%\pcnotify` se queda y te dice dónde está.
- Actualizar: pulsa «Salir» en la página y vuelve a pegar la línea de instalación; instala la última versión y conserva la configuración y el enlace.

### Qué envía y a quién

Solo envía, al servicio de enlace de Pendi, el tipo de aviso (partida encontrada o partida empezada) junto con el identificador del enlace y el secreto de este PC, que viven en `%APPDATA%\pcnotify\config.json`. El servicio lo entrega a la cuenta que enlazaste. No envía tu nombre, tus partidas ni nada más del juego. Para saber cuándo empieza la partida lee el reloj del juego en este mismo PC, y no lo guarda ni lo envía.

### `--dry`

Con `--dry` el programa solo avisa y no acepta la partida por ti.

Licencia: MIT

## English

A small Windows program that alerts your phone when your match is found and when it starts.
It links once to your Pendi account by scanning a QR code, with no new account of any kind.
It is free, needs no administrator rights and only uses Python.

### Install

Press Windows + R, paste this whole line and press Enter. It also works in PowerShell or the Command Prompt.

    powershell -NoExit -NoProfile -ExecutionPolicy Bypass -Command "ri ~\pcnotify-install.ps1 -ea 0; irm https://raw.githubusercontent.com/ElkinDev/pcnotify/main/install.ps1 -OutFile ~\pcnotify-install.ps1; ~\pcnotify-install.ps1"

The line saves the installer as `pcnotify-install.ps1` in your user folder and runs it from there; you can delete that file when it is done.

The window stays open at the end so you can read the result; close it when it is done.

If Windows says it cannot access the file or the antivirus stops the line, nothing was installed: update the antivirus definitions and try again, or download `install.ps1` from the repository page, click it with the right button and choose "Run with PowerShell".

If you see "'irm' is not recognized as an internal or external command", you pasted only the short part into the Command Prompt: use the whole line above.

The installer looks for Python 3.10 or newer; when there is none it installs it with winget for your user only, or points you to https://www.python.org/downloads/ when winget is missing. Then it installs pcnotify with pip, leaves a `pcnotify` shortcut on the Desktop and in the Start menu, and starts it.

In PowerShell: to see what it would do without changing anything, set `$env:PCNOTIFY_DRYRUN = "1"` before pasting the line. To leave it stopped at the end, set `$env:PCNOTIFY_NOSTART = "1"`.

### Link

When it starts, the browser opens a local page with a QR code. Scan it with the phone's camera: the Pendi app opens and asks you to confirm the link. The page shows when the link is done.

The QR code and the key show for a minute when «Show the code» is pressed, and then hide again.

The button at the top right switches between the light and the dark theme, and the program keeps the choice for the next time.

### Start, stop, uninstall

- Start: the `pcnotify` shortcut, or `pythonw -m pcnotify`, which is what the shortcut runs; with no console, it tells you in a window when it is already running or cannot start. To see it in a console: `python -m pcnotify`.
- Stop: press «Quit» on the program's page; if it is not open, starting the program again opens it. As a last resort, end the `pythonw.exe` process in Task Manager.
- Pause: «Pause alerts», on the page's «This PC» card, stops reading the game client: it accepts no match and sends no alert to your phone, and the page stays open. «Resume alerts» reads it again. The pause is not kept: every start of the program is active again.
- Start with Windows: the «Start with Windows» switch, on the same card, is off until you turn it on. When it is on, the program starts by itself when you sign in to Windows, for your user only, without opening the browser or the page.
- Activity: the page's «Activity» card lists what the program did since it started: the game client found and lost, each phase, the match accepted and the alert sent. It keeps the last 50 lines, in memory only.
- Uninstall: paste `powershell -NoExit -NoProfile -ExecutionPolicy Bypass -Command "ri ~\pcnotify-uninstall.ps1 -ea 0; irm https://raw.githubusercontent.com/ElkinDev/pcnotify/main/uninstall.ps1 -OutFile ~\pcnotify-uninstall.ps1; ~\pcnotify-uninstall.ps1"` the same way as the install line; it leaves `pcnotify-uninstall.ps1` in your user folder, which you can delete. It removes the package from the same Python that installed it, checks that it is gone, removes both shortcuts and removes the start with Windows when it is on; the configuration in `%APPDATA%\pcnotify` stays, and it tells you where it is.
- Update: press "Quit" on the page and paste the install line again; it installs the latest version and keeps the configuration and the link.

### What it sends and to whom

It only sends, to Pendi's link service, the alert kind (match found or match started) together with the link id and this PC's secret, which live in `%APPDATA%\pcnotify\config.json`. The service delivers it to the account you linked. It sends no name, no match history and nothing else from the game. To tell when the match starts it reads the game's clock on this same PC, and neither keeps nor sends it.

### `--dry`

With `--dry` the program only alerts and never accepts the match for you.

License: MIT

## Developer commands

    python -m pcnotify
    python -m pcnotify ping <kind>

The first loads the config, starts the page and opens it in the default browser while the PC is not paired.
It also watches the game client on this PC: when a match is found it accepts after a short random delay, beeps, and sends the found-match alert; when the loading screen starts it beeps, and when the match itself starts it beeps and sends the started alert.
Add `--dry` to watch and alert without accepting. Add `--quiet` for the start by the system at logon, the line the start with Windows switch writes (`pythonw -m pcnotify --quiet`): it never opens the browser, a start that ends well shows no window, and a second quiet start prints `already running` and exits. One copy runs per config folder: a second start opens the page of the one that runs and exits. Ctrl+C stops the page and the watcher together.
The second sends one alert with the stored pair and prints one line with the answer.

Both commands take `--data-dir <dir>`, used instead of `%APPDATA%` (the config file lives in a folder under it), and `--worker <address>`, used instead of the pairing service's address and accepted only as `http://127.0.0.1:<port>` or `http://localhost:<port>`, so a test run never reaches the real service. `--client-lockfile <file>` replaces the game client's lockfile, with no read of the running processes, and the client it names is reached over plain http on 127.0.0.1, so a test run never reaches a real client.

    python -m unittest discover -s tests -v
