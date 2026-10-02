"""The commands.

    python -m <package>              the pairing page and the watcher of the game client, together
    python -m <package> --dry        the same, but the watcher never accepts: it alerts at the queue pop
    python -m <package> --quiet      the same, started by the system at logon: no browser, and no message box
                                     unless the start fails
    python -m <package> --no-update  the same, with no check of PyPI for a newer version (update.py)
    python -m <package> ping <kind>  one alert with the stored pair, one fixed line per answer

The page and the watcher stop together on Ctrl+C, on Ctrl+Break, on the page's quit button and on the quit of
the icon by the clock, which remove the run file and exit 0. One instance per config folder: a second start
opens the running page and exits 0, and one made while that program is still closing after its quit waits for
it to end and starts. A start of a newer version over a running older copy replaces it: the older copy is asked to
stop through its page's /replace with the run file's secret, or ended by its pid when it has no secret or its page
does not answer, and this start then claims the run file as a first one does. A restart the page asks for
(«Reiniciar ahora», once a newer version is installed, or the
icon's «Reiniciar para actualizar») starts a new copy once the run file is released: without --quiet, so its page
opens, and with the --dry, --data-dir, --worker and --client-lockfile of this one.
Where no console is attached (a pythonw start), the line a start ends on is also shown in a
message box. `--data-dir`, `--worker` and `--client-lockfile` are the
test overrides, loopback only, so a test run reads neither the profile, the Worker nor the client's files.
"""
import argparse
import http.client
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from . import alert, client, config, page, pairing, runfile, update, watcher, worker
from .autostart import Autostart
from .tray import Tray

TICK_SECONDS = 1.0
STOP_SECONDS = 5.0
# How long a start waits for a copy that is closing to let its run file go: one tick and the two STOP_SECONDS waits
# of its stop, with room.
CLOSING_WAIT_SECONDS = 15.0
REFUSED_LINE = "refused: the pairing of this PC was not accepted; link it again from the page"
NOT_LINKED_LINE = "not linked: start the program without arguments and link this PC first"
ALREADY_RUNNING_LINE = "already running: opening the page of the program that runs"
# A quiet second start opens nothing, so its line promises nothing.
QUIET_RUNNING_LINE = "already running"
PAGE_NOT_KNOWN_LINE = ("already running: the page of the program that runs is not known yet; start it again in a "
                       "moment to open it")
CLAIM_FAILED_LINE = "cannot start: the run file cannot be replaced: {path}"
FOLDER_FAILED_LINE = "cannot start: the config folder cannot be written: {path}"
RESTART_FAILED_LINE = "cannot restart: the new copy did not start; start the program again"
# The line a start that replaced an older copy shows, in the page's language: the older copy's version, «desconocida»
# or "unknown" for a record with none, then this one's.
REPLACED_LINES = {"es": "Se reemplazó la copia anterior ({old}) por esta ({new}).",
                  "en": "The older copy ({old}) was replaced by this one ({new})."}
UNKNOWN_VERSION = {"es": "desconocida", "en": "unknown"}
# The line a start prints when the older copy it would replace cannot be ended, in the page's language, with the
# system's reason.
CLOSE_REFUSED_LINES = {"es": "No se pudo cerrar la copia anterior: {reason}",
                       "en": "The older copy could not be closed: {reason}"}
REPLACE_TIMEOUT_SECONDS = 5.0  # the replace post to the older copy's page
_MB_ICONINFORMATION = 0x40
_MB_SETFOREGROUND = 0x10000
# The restart's new copy: no console, its own process group, out of this one's.
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_PROCESS_GROUP = 0x00000200
# The icon by the clock's picture: pendify.ico beside the modules, the file install.ps1 points the shortcuts at.
ICON_PATH = Path(__file__).with_name("pendify.ico")


def _data_dir(value):
    if not value.strip():
        raise argparse.ArgumentTypeError("the data directory is empty")
    return Path(value)


def _client_lockfile(value):
    if not value.strip():
        raise argparse.ArgumentTypeError("the lockfile path is empty")
    return Path(value)


def _worker_address(value):
    try:
        return worker.loopback_base(value)
    except ValueError as refused:
        raise argparse.ArgumentTypeError(str(refused)) from None


def _arguments(argv):
    # The options are accepted before and after the command; SUPPRESS keeps one side from erasing the other.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", type=_data_dir, default=argparse.SUPPRESS,
                        help="a directory used instead of %%APPDATA%%, for test runs")
    common.add_argument("--worker", type=_worker_address, default=argparse.SUPPRESS,
                        help="http://127.0.0.1:<port> or http://localhost:<port> instead of the Worker, for test runs")
    common.add_argument("--client-lockfile", type=_client_lockfile, default=argparse.SUPPRESS,
                        help="a lockfile read instead of the client's, its client reached over plain http on "
                             "127.0.0.1, for test runs")
    parser = argparse.ArgumentParser(prog=f"python -m {__package__}", parents=[common],
                                     description="Pairs this PC by a QR on a loopback page, watches the game "
                                                 "client and sends alerts.")
    parser.add_argument("--dry", action="store_true", help="watch and alert, but never accept")
    parser.add_argument("--quiet", action="store_true",
                        help="a start by the system: no browser, and no message box for a start that ends well")
    parser.add_argument("--no-update", action="store_true",
                        help="no check of PyPI for a newer version in this run")
    commands = parser.add_subparsers(dest="command")
    ping = commands.add_parser("ping", parents=[common], help="send one alert with the stored pair")
    ping.add_argument("kind", choices=worker.KINDS)
    return parser.parse_args(argv)


def _ping_line(result):
    if isinstance(result, worker.Sent):
        return "sent"
    if isinstance(result, worker.Refused):
        return REFUSED_LINE
    if isinstance(result, worker.NotDelivered):
        return f"not delivered (HTTP {result.status})"
    return f"failed: {result.reason}"


def _ping(store, base, kind, timeout):
    pair = store.read()  # never a first load: a ping mints nothing
    if pair is None or pair.link_id is None:
        print(NOT_LINKED_LINE)
        return 2
    result = worker.ping(pair.link_id, pair.secret, kind, base=base, timeout=timeout)
    print(_ping_line(result))
    return 0 if isinstance(result, worker.Sent) else 1


def _watcher(args, store, state, base, timeout, stop, delay, beep):
    """The watcher and its alert. The test override reads its one lockfile, with no process read, and
    reaches its client over plain http on 127.0.0.1."""
    lockfile = getattr(args, "client_lockfile", None)
    if lockfile is None:
        credentials, addresses = client.ClientCredentials(), client.real_addresses
    else:
        credentials, addresses = client.ClientCredentials((str(lockfile),), run=None), client.loopback_addresses
    alerter = alert.Alerter(store, state, lambda link_id, secret, kind: worker.ping(
        link_id, secret, kind, base=base, timeout=timeout), beep=beep)
    return watcher.Watcher(credentials, alerter, accept=not args.dry, addresses=addresses, delay=delay,
                           stop=stop), alerter


def _console_attached():
    """False only on Windows with no terminal on stdout and no console window: a pythonw start, where a
    printed line reaches nobody."""
    if os.name != "nt":
        return True
    if sys.stdout is not None and sys.stdout.isatty():
        return True
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetConsoleWindow.restype = wintypes.HWND
    return bool(kernel32.GetConsoleWindow())


def _message_box(line):
    """The line in a Windows message box, the one place a start with no console can show it."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    user32.MessageBoxW.argtypes = (wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT)
    user32.MessageBoxW(None, line, __package__, _MB_ICONINFORMATION | _MB_SETFOREGROUND)


def _ends(line, code, show):
    """The line a start ends on: printed as ever, and shown where no console is attached."""
    print(line, flush=True)
    show(line)
    return code


def _restart(args, spawn, show, opens_page):
    """The restart asked for, once the run file is free: a new copy of this program, detached and with no console,
    which claims the run file as a first start does. One that opens its page, as every restart asked today does,
    goes without --quiet; one that does not keeps this run's --quiet. This run's --dry, --data-dir, --worker and
    --client-lockfile go with it either way."""
    command = [sys.executable, "-m", __package__] + (["--quiet"] if args.quiet and not opens_page else [])
    command += ["--dry"] if args.dry else []
    for option in ("data_dir", "worker", "client_lockfile"):
        if hasattr(args, option):  # the parser's SUPPRESS: present only when this run was given it
            command += ["--" + option.replace("_", "-"), str(getattr(args, option))]
    try:
        spawn(command, creationflags=_DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP, close_fds=True)
    except OSError:
        return _ends(RESTART_FAILED_LINE, 1, show)
    return 0


def _replaces(holder):
    """True when this copy is newer than `holder`, a live holder that is not closing: X.Y.Z compared as tuples of
    ints, and a holder with no version, as an older copy writes its record, older than any. A copy whose own version
    is not X.Y.Z replaces nothing."""
    if update._parts(update.RUNNING_VERSION) is None:
        return False
    return holder["version"] is None or update.newer(update.RUNNING_VERSION, holder["version"])


def _ask_replace(holder):
    """True when the holder's page answered 202 to POST /replace with the run file's secret; False for any other
    answer, none, or no port."""
    if holder["port"] is None:
        return False
    request = urllib.request.Request(f"http://{page.ADDRESS}:{holder['port']}/replace", method="POST",
                                     data=urllib.parse.urlencode({"secret": holder["secret"]}).encode())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # a loopback address never goes through one
    try:
        with opener.open(request, timeout=REPLACE_TIMEOUT_SECONDS) as answer:
            return answer.status == 202
    except (OSError, http.client.HTTPException):  # a refusal (urllib.error.HTTPError), no answer in time, a broken one
        return False


def _kept_language(store):
    """The page's language before the page is made, with no browser to ask: the kept choice, else Spanish, as the
    icon's words. A config file that cannot be read gives Spanish: the choice only words one line."""
    try:
        return store.read_lang() or page.language(None)
    except (OSError, ValueError):
        return page.language(None)


def _replace(run, holder, terminate, store):
    """The older `holder` dealt with, then the run file claimed again: (the claim's answer, `holder` when this start
    replaced it, else None). It is asked through its page's /replace; with no secret in its record or no 202 from its
    page, `terminate` is asked to end it by its pid, which it does only for the process its record names
    (runfile.terminate):
    - a 202, or ended: once the run file no longer names it as a live process within CLOSING_WAIT_SECONDS, its file
      gone or left stale by its death, the file is claimed; a holder still alive then is handed back as it was read;
    - not-ours: the pid names another process now, so the file is stale: evicted while it still holds that record,
      then claimed as a first start claims it;
    - refused: the reason printed on one line and the file waited for as long, since the pid may be dying under
      another start's terminate, then claimed again: a holder that still stands is read afresh, never the snapshot."""
    if holder["secret"] is None or not _ask_replace(holder):
        answer = terminate(holder["pid"], holder["started"], run.path)
        if answer == runfile.NOT_OURS:
            run.evict(holder)
            return run.claim(), holder
        if answer == runfile.REFUSED:
            reason = answer.error.strerror or answer.error
            print(CLOSE_REFUSED_LINES[_kept_language(store)].format(reason=reason), flush=True)
            run.wait_released(holder, CLOSING_WAIT_SECONDS)
            return run.claim(), None
    if run.wait_released(holder, CLOSING_WAIT_SECONDS):
        return run.claim(), holder
    return holder, None


def _ready_version(updater):
    """The version an install made ready, for the icon's item; None in any other state."""
    seen = updater.snapshot()
    return seen["version"] if seen["state"] == update.READY else None


def _serve(args, store, base, timeout, opener, stop, delay, beep, show, clock, sleep, autostart, tray, updater,
           spawn, terminate):
    # A quiet start is the system's, at logon: it never opens the browser, and a start that ends well shows no box.
    calm = (lambda line: None) if args.quiet else show
    run = runfile.RunFile(store.path.parent, clock=clock, sleep=sleep)
    replaced = None  # the record of the older copy this start replaced
    try:
        holder = run.claim()
        if holder is not None and holder["closing"] and run.wait_released(holder, CLOSING_WAIT_SECONDS):
            holder = run.claim()  # the closing copy let its file go: this start claims it as a first one does
        # A running older copy is replaced (_replace); a holder that still stands after it keeps the road below,
        # its page opened.
        if holder is not None and not holder["closing"] and _replaces(holder):
            holder, replaced = _replace(run, holder, terminate, store)
    except runfile.FolderNotWritable as refused:
        return _ends(FOLDER_FAILED_LINE.format(path=refused.filename), 1, show)
    except OSError:
        return _ends(CLAIM_FAILED_LINE.format(path=run.path), 1, show)
    if holder is not None:
        if holder["closing"]:  # still closing at the bound: its page is going, so it is never opened
            return _ends(QUIET_RUNNING_LINE if args.quiet else PAGE_NOT_KNOWN_LINE, 0, calm)
        port = holder["port"] if holder["port"] is not None else run.holder_port(holder)
        if port is None:  # a winner with no page yet: nothing is opened; a quiet start promises no page either
            return _ends(QUIET_RUNNING_LINE if args.quiet else PAGE_NOT_KNOWN_LINE, 0, calm)
        if args.quiet:  # the system's start at logon: nothing opened, nothing shown, a line of its own
            print(QUIET_RUNNING_LINE, flush=True)
            return 0
        print(ALREADY_RUNNING_LINE, flush=True)
        opener(f"http://{page.ADDRESS}:{port}/")
        show(ALREADY_RUNNING_LINE)
        return 0
    updating = None
    try:
        mode = update.OFF if args.no_update else store.read_update()
        state = pairing.PairingState(store, lambda secret: worker.check(secret, base=base, timeout=timeout))
        watch, alerter = _watcher(args, store, state, base, timeout, stop, delay, beep)

        def quit_page():
            # The page's quit marks the record closing at once, since a tick can be held in a slow check before
            # the finally runs, then sets the same stop event as Ctrl+C: the watcher, the page and the run file
            # end below.
            try:
                run.mark_closing()
            except OSError:
                pass  # unmarked, the finally marks it again; the stop goes on
            stop.set()

        pairing_page = page.PairingPage(state, watch=watch.snapshot, on_quit=quit_page, on_pause=watch.pause,
                                        on_resume=watch.resume, autostart=autostart, events=watch.events)
        pairing_page.run = run  # its /replace answers the secret the publish below writes
        url = pairing_page.start()
        # The update's rounds on their own thread (update.py), built once the page serves; its restart is the
        # page's quit, after the flag read below once the run file is free; pip's output goes to the config folder.
        updating = updater(update.RUNNING_VERSION, mode, restart=quit_page, folder=store.path.parent)
        pairing_page.updater = updating
        # The icon by the clock, under --quiet as well: the way to stop a copy the Run key started. Its words are
        # the page's, in the language the page resolves with no browser to ask: the kept choice, else Spanish.
        icon = tray(url, on_open=lambda: opener(url), on_pause=watch.pause, on_resume=watch.resume,
                    on_quit=quit_page, paused=lambda: watch.paused,
                    words=lambda: page.WORDS[pairing_page.language_of(None)], icon_path=ICON_PATH,
                    # «Reiniciar para actualizar a X» once an install is ready, the page's own restart
                    update_ready=lambda: _ready_version(updating), on_restart=updating.restart)
        watching = threading.Thread(target=watch.run, daemon=True)
        try:
            icon.start()
            if mode != update.OFF:
                updating.start()
            run.publish(pairing_page.port)
            if replaced is not None:  # in the page's language, as the icon's words; a box never holds the start
                lang = pairing_page.language_of(None)
                line = REPLACED_LINES[lang].format(old=replaced["version"] or UNKNOWN_VERSION[lang],
                                                   new=update.RUNNING_VERSION)
                print(line, flush=True)
                threading.Thread(target=calm, args=(line,), daemon=True).start()
            print(f"page: {url}", flush=True)
            if not args.quiet and not opener(url):  # a start by a person opens the page, linked or not
                print("open the address above in a browser", flush=True)
            watching.start()
            while True:
                state.tick()
                if stop.wait(TICK_SECONDS):
                    break
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            updating.close()  # the restart gate shuts at the quit
            # First, so a start made while this one stops waits for it instead of opening a closing page: Ctrl+C
            # and Ctrl+Break are marked only here, and the page's quit, marked already, is marked again.
            try:
                run.mark_closing()
            except OSError:
                pass  # unmarked, a start meanwhile opens this page as before; the stop goes on
            if watching.is_alive():
                watching.join(STOP_SECONDS)
            alerter.flush(STOP_SECONDS)
            pairing_page.close()
            icon.close()
            # An install under way is waited for before the run file goes, so a quit does not end this copy while
            # pip swaps the files; past the bound pip goes on alone, its output in a file.
            updating.wait(update.INSTALL_JOIN_SECONDS)
    finally:
        run.release()
    if updating is not None and updating.restart_requested.is_set():
        return _restart(args, spawn, show, updating.restart_opens_page)
    return 0


def _break_as_interrupt():
    """Ctrl+Break stops the program as Ctrl+C does; only on Windows and only on the main thread."""
    if not hasattr(signal, "SIGBREAK") or threading.current_thread() is not threading.main_thread():
        return None
    return signal.signal(signal.SIGBREAK, signal.default_int_handler)


def main(argv=None, *, opener=webbrowser.open, stop=None, timeout=worker.TIMEOUT_SECONDS, delay=None,
         beep=alert.beep, box=_message_box, autostart=None, console=_console_attached, clock=time.monotonic,
         sleep=time.sleep, tray=Tray, updater=update.Updater, spawn=subprocess.Popen, terminate=runfile.terminate):
    """`beep`, `box` and `autostart` are the seams of the sound, of the message box and of the start with Windows
    (the per-user Run value when None): a test run passes silent ones and a fake. `console` says whether a printed
    line reaches anybody; `clock` and `sleep` time the run file's re-reads. `tray` makes the icon by the clock, the
    seam a test run fills with a fake, so no test shows a real icon. `updater` makes the update and `spawn` starts
    the restart's new copy, the seams a test run fills with fakes, so no test reaches PyPI, runs pip or starts a
    copy. `terminate` ends an older copy by its pid when it cannot be asked to stop, the seam every test run fakes, so
    no test ends a process."""
    args = _arguments(sys.argv[1:] if argv is None else argv)
    store = config.ConfigStore(getattr(args, "data_dir", None) or config.default_base_dir())
    base = getattr(args, "worker", worker.BASE_URL)

    def show(line):  # a start's last line, also in a message box where no console is attached
        if not console():
            box(line)

    try:
        if args.command == "ping":
            return _ping(store, base, args.kind, timeout)
        previous = _break_as_interrupt()
        try:
            return _serve(args, store, base, timeout, opener, stop or threading.Event(),
                          delay or watcher.accept_delay, beep, show, clock, sleep,
                          autostart if autostart is not None else Autostart(), tray, updater, spawn, terminate)
        finally:
            if previous is not None:
                signal.signal(signal.SIGBREAK, previous)
    except config.ConfigError as failure:  # at start or mid-run: its one sentence, never a traceback
        print(failure)
        if args.command != "ping":  # a start ends on it
            show(str(failure))
        return 1


if __name__ == "__main__":
    sys.exit(main())
