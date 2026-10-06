# PendiFy

## Español

Un programa pequeño para Windows que avisa en tu teléfono cuando se encuentra tu partida y cuando empieza.
Se enlaza una sola vez con tu cuenta de Pendi leyendo un código QR, sin crear ninguna cuenta nueva.
Es gratis, no pide permisos de administrador y solo usa Python.

![La página de PendiFy en el navegador: este PC enlazado a la cuenta, la lista de actividad y el código QR que abre pendiapp.com.](https://raw.githubusercontent.com/ElkinDev/PendiFy/main/docs/demo.jpeg)

### Instalar

Pulsa Windows + R, pega esta línea completa y pulsa Enter. También sirve en PowerShell o en el Símbolo del sistema.

    powershell -NoExit -NoProfile -ExecutionPolicy Bypass -Command "ri ~\pendify-install.ps1 -ea 0; irm https://raw.githubusercontent.com/ElkinDev/PendiFy/main/install.ps1 -OutFile ~\pendify-install.ps1; ~\pendify-install.ps1"

La línea guarda el instalador como `pendify-install.ps1` en tu carpeta de usuario y lo ejecuta desde ahí; puedes borrar ese archivo cuando termine. Si PendiFy ya está abierto, la línea instala la versión nueva y la copia nueva reemplaza a la anterior por sí sola.

La ventana queda abierta al terminar para que leas el resultado; ciérrala cuando acabe.

Si Windows dice que no puede acceder al archivo o el antivirus detiene la línea, no se instaló nada: actualiza las definiciones del antivirus y vuelve a intentarlo, o descarga `install.ps1` desde la página del repositorio, haz clic en él con el botón derecho y elige «Ejecutar con PowerShell».

Si ves "'irm' no se reconoce como un comando interno o externo", pegaste solo la parte corta en el Símbolo del sistema: usa la línea completa de arriba.

El instalador busca Python 3.10 o más nuevo; si no lo hay lo instala con winget solo para tu usuario, o te indica https://www.python.org/downloads/ cuando winget no existe. Después instala PendiFy con pip, deja un acceso directo `PendiFy` en el Escritorio y en el menú Inicio, y lo inicia.

En PowerShell: para ver qué haría sin cambiar nada: `$env:PENDIFY_DRYRUN = "1"` antes de pegar la línea. Para no iniciarlo al final: `$env:PENDIFY_NOSTART = "1"`.

Otra forma, sin ejecutar ningún script: si el PC ya tiene Python 3.10 o más nuevo, o si en él no se permite ejecutar scripts, instálalo con pip e inícialo:

    python -m pip install --upgrade pendify
    python -m pendify

Si Windows responde que no encuentra `python`, usa `py` en su lugar: `py -m pip install --upgrade pendify` y `py -m pendify`.

Así no se crean los accesos directos en el Escritorio ni en el menú Inicio, y el inicio con Windows queda en el interruptor de la página. Para actualizar, pulsa «Salir» en la página, ejecuta otra vez la misma línea de pip y vuelve a iniciarlo. Para quitarlo, apaga primero el interruptor «Iniciar con Windows» en la página, pulsa «Salir» y después ejecuta `python -m pip uninstall pendify`. La configuración en `%APPDATA%\pendify`, con el enlace con el teléfono, se queda; puedes borrar esa carpeta a mano.

El programa revisa PyPI cada seis horas y, cuando hay una versión más nueva, la instala en segundo plano. La página ofrece entonces «Reiniciar ahora» para empezar a usarla. Si la instalación falla, la página ofrece «Reintentar» en cualquier modo y, la primera vez que falla una versión, la siguiente revisión llega a los diez minutos. Para apagarlo, inícialo con `--no-update` o escribe `"update": "off"` en `%APPDATA%\pendify\config.json`. Las actualizaciones automáticas se instalan para tu usuario (--user).

### Enlazar

Al iniciarse, el navegador abre una página local con un código QR. Escanéalo con la cámara del teléfono: se abre la app Pendi y te pide confirmar el enlace. La página muestra cuando el enlace quedó hecho.

El código QR y la clave están ocultos; se muestran durante un minuto al pulsar «Mostrar el código», y la página dice para qué sirve el código.

El botón de arriba a la derecha cambia entre el tema claro y el oscuro, y el programa guarda la elección para la próxima vez.

### Iniciar, detener, desinstalar

- Iniciar: el acceso directo `PendiFy`, o `pythonw -m pendify`, que es lo que ejecuta el acceso directo; sin consola, si ya está abierto o no puede iniciarse te lo dice en una ventana. Para verlo en una consola: `python -m pendify`.
- Detener: pulsa «Salir» en la página del programa; si no la tienes abierta, iniciarlo otra vez la abre. Como último recurso, cierra el proceso `pythonw.exe` en el Administrador de tareas.
- Icono junto al reloj: un clic en el icono de PendiFy junto al reloj abre un menú para abrir la página, pausar o reanudar los avisos y salir del programa, también con la página y el navegador cerrados.
- Pausar: «Pausar avisos», en la tarjeta «Este PC» de la página, deja de leer el cliente del juego: no acepta partidas ni avisa a tu teléfono, y la página sigue abierta. «Reanudar avisos» vuelve a leerlo. La pausa no se guarda: cada vez que el programa se inicia, empieza activo.
- Iniciar con Windows: el interruptor «Iniciar con Windows», en la misma tarjeta, está apagado hasta que lo enciendas. Encendido, el programa empieza solo al iniciar sesión en Windows, solo para tu usuario y sin abrir el navegador ni la página.
- Actividad: la tarjeta «Actividad» de la página lista lo que el programa hizo desde que empezó: el cliente del juego encontrado y perdido, cada fase, la partida aceptada y el aviso enviado. Guarda las últimas 50 líneas, solo en memoria.
- Idioma: la página sigue el idioma del navegador hasta que eliges ES o EN con el selector de arriba, y la elección se guarda para este PC.
- Desinstalar: pega `powershell -NoExit -NoProfile -ExecutionPolicy Bypass -Command "ri ~\pendify-uninstall.ps1 -ea 0; irm https://raw.githubusercontent.com/ElkinDev/PendiFy/main/uninstall.ps1 -OutFile ~\pendify-uninstall.ps1; ~\pendify-uninstall.ps1"` igual que la línea de instalación; deja `pendify-uninstall.ps1` en tu carpeta de usuario, que puedes borrar. Quita el paquete del mismo Python que lo instaló, comprueba que ya no está, quita los dos accesos directos y quita el inicio con Windows si estaba encendido; la configuración en `%APPDATA%\pendify` se queda y te dice dónde está.
- Actualizar: pulsa «Salir» en la página y vuelve a pegar la línea de instalación; instala la última versión y conserva la configuración y el enlace.

### Qué envía y a quién

Solo envía, al servicio de enlace de Pendi, el tipo de aviso (partida encontrada o partida empezada) junto con el identificador del enlace y el secreto de este PC, que viven en `%APPDATA%\pendify\config.json`. El servicio lo entrega a la cuenta que enlazaste. No envía tu nombre, tus partidas ni nada más del juego. Para saber cuándo empieza la partida lee el reloj del juego en este mismo PC, y no lo guarda ni lo envía. Si el juego no da su reloj, lee en su lugar el registro propio del juego en este mismo PC, solo la hora de dos clases de línea, y no guarda ni envía nada de él. Una vez empezada la partida, pregunta al cliente del juego una vez cada cinco segundos, solo para ver cuándo termina.

### `--dry`

Con `--dry` el programa solo avisa y no acepta la partida por ti.

Licencia: MIT

## English

A small Windows program that alerts your phone when your match is found and when it starts.
It links once to your Pendi account by scanning a QR code, with no new account of any kind.
It is free, needs no administrator rights and only uses Python.

![The PendiFy page in the browser: this PC linked to the account, the activity list and the QR code that opens pendiapp.com.](https://raw.githubusercontent.com/ElkinDev/PendiFy/main/docs/demo.jpeg)

### Install

Press Windows + R, paste this whole line and press Enter. It also works in PowerShell or the Command Prompt.

    powershell -NoExit -NoProfile -ExecutionPolicy Bypass -Command "ri ~\pendify-install.ps1 -ea 0; irm https://raw.githubusercontent.com/ElkinDev/PendiFy/main/install.ps1 -OutFile ~\pendify-install.ps1; ~\pendify-install.ps1"

The line saves the installer as `pendify-install.ps1` in your user folder and runs it from there; you can delete that file when it is done. If PendiFy is already open, the line installs the new version and the new copy replaces the older one by itself.

The window stays open at the end so you can read the result; close it when it is done.

If Windows says it cannot access the file or the antivirus stops the line, nothing was installed: update the antivirus definitions and try again, or download `install.ps1` from the repository page, click it with the right button and choose "Run with PowerShell".

If you see "'irm' is not recognized as an internal or external command", you pasted only the short part into the Command Prompt: use the whole line above.

The installer looks for Python 3.10 or newer; when there is none it installs it with winget for your user only, or points you to https://www.python.org/downloads/ when winget is missing. Then it installs PendiFy with pip, leaves a `PendiFy` shortcut on the Desktop and in the Start menu, and starts it.

In PowerShell: to see what it would do without changing anything, set `$env:PENDIFY_DRYRUN = "1"` before pasting the line. To leave it stopped at the end, set `$env:PENDIFY_NOSTART = "1"`.

Another way, with no script at all: on a PC that already has Python 3.10 or newer, or where scripts are not allowed, install it with pip and start it:

    python -m pip install --upgrade pendify
    python -m pendify

If Windows answers that `python` was not found, use `py` in its place: `py -m pip install --upgrade pendify` and `py -m pendify`.

This way leaves no shortcut on the Desktop or in the Start menu, and the start with Windows is the switch on the page. To update, press «Quit» on the page, run the same pip line again and start it again. To remove it, first turn off the «Start with Windows» switch on the page, press «Quit», then run `python -m pip uninstall pendify`. The configuration in `%APPDATA%\pendify`, with the link to the phone, stays; you can delete that folder by hand.

The program checks PyPI every six hours and, when a newer version exists, installs it in the background. The page then offers «Restart now» to start using it. If the install fails, the page offers «Try again» in either mode and, the first time a version fails, the next check comes ten minutes later. To turn this off, start it with `--no-update` or write `"update": "off"` in `%APPDATA%\pendify\config.json`. Automatic updates install for your user (--user).

### Link

When it starts, the browser opens a local page with a QR code. Scan it with the phone's camera: the Pendi app opens and asks you to confirm the link. The page shows when the link is done.

The QR code and the key are hidden; they show for a minute when «Show the code» is pressed, and the page says what the code is for.

The button at the top right switches between the light and the dark theme, and the program keeps the choice for the next time.

### Start, stop, uninstall

- Start: the `PendiFy` shortcut, or `pythonw -m pendify`, which is what the shortcut runs; with no console, it tells you in a window when it is already running or cannot start. To see it in a console: `python -m pendify`.
- Stop: press «Quit» on the program's page; if it is not open, starting the program again opens it. As a last resort, end the `pythonw.exe` process in Task Manager.
- Icon by the clock: a click on the PendiFy icon by the clock opens a menu that opens the page, pauses or resumes the alerts and quits the program, also with the page and the browser closed.
- Pause: «Pause alerts», on the page's «This PC» card, stops reading the game client: it accepts no match and sends no alert to your phone, and the page stays open. «Resume alerts» reads it again. The pause is not kept: every start of the program is active again.
- Start with Windows: the «Start with Windows» switch, on the same card, is off until you turn it on. When it is on, the program starts by itself when you sign in to Windows, for your user only, without opening the browser or the page.
- Activity: the page's «Activity» card lists what the program did since it started: the game client found and lost, each phase, the match accepted and the alert sent. It keeps the last 50 lines, in memory only.
- Language: the page follows the browser's language until you choose ES or EN with the switch at the top, and the choice is kept for this PC.
- Uninstall: paste `powershell -NoExit -NoProfile -ExecutionPolicy Bypass -Command "ri ~\pendify-uninstall.ps1 -ea 0; irm https://raw.githubusercontent.com/ElkinDev/PendiFy/main/uninstall.ps1 -OutFile ~\pendify-uninstall.ps1; ~\pendify-uninstall.ps1"` the same way as the install line; it leaves `pendify-uninstall.ps1` in your user folder, which you can delete. It removes the package from the same Python that installed it, checks that it is gone, removes both shortcuts and removes the start with Windows when it is on; the configuration in `%APPDATA%\pendify` stays, and it tells you where it is.
- Update: press "Quit" on the page and paste the install line again; it installs the latest version and keeps the configuration and the link.

### What it sends and to whom

It only sends, to Pendi's link service, the alert kind (match found or match started) together with the link id and this PC's secret, which live in `%APPDATA%\pendify\config.json`. The service delivers it to the account you linked. It sends no name, no match history and nothing else from the game. To tell when the match starts it reads the game's clock on this same PC, and neither keeps nor sends it. When the game gives no clock, it reads the game's own log on this same PC instead, only the time stamps of two kinds of line, and neither keeps nor sends any of it.

### `--dry`

With `--dry` the program only alerts and never accepts the match for you.

License: MIT

## Developer commands

    python -m pendify
    python -m pendify ping <kind>

The first loads the config, starts the page and opens it in the default browser, paired or not.
It also watches the game client on this PC: when a match is found it accepts after a short random delay, beeps, and sends the found-match alert; when the loading screen starts it beeps, and when the match itself starts it beeps and sends the started alert. Once the match has started it asks the game client once every five seconds, only to see the match end.
Add `--dry` to watch and alert without accepting. Add `--quiet` for the start by the system at logon, the line the start with Windows switch writes (`pythonw -m pendify --quiet`): it never opens the browser, a start that ends well shows no window, and a second quiet start prints `already running` and exits. One copy runs per config folder: a second start opens the page of the one that runs and exits. Ctrl+C stops the page and the watcher together.
The second sends one alert with the stored pair and prints one line with the answer.

Both commands take `--data-dir <dir>`, used instead of `%APPDATA%` (the config file lives in a folder under it), and `--worker <address>`, used instead of the pairing service's address and accepted only as `http://127.0.0.1:<port>` or `http://localhost:<port>`, so a test run never reaches the real service. `--client-lockfile <file>` replaces the game client's lockfile, with no read of the running processes, and the client it names is reached over plain http on 127.0.0.1, so a test run never reaches a real client.

    python -m unittest discover -s tests -v
