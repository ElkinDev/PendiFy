"""The commands.

    python -m <package>              the pairing page and the watcher of the game client, together
    python -m <package> --dry        the same, but the watcher never accepts: it alerts at the queue pop
    python -m <package> --quiet      the same, started by the system at logon: no browser, and no message box
                                     unless the start fails
    python -m <package> ping <kind>  one alert with the stored pair, one fixed line per answer

The page and the watcher stop together on Ctrl+C, on Ctrl+Break and on the page's quit button, which
removes the run file and exits 0. One instance per config folder: a second start opens the running page
and exits 0, and one made while that program is still closing after its quit waits for it to end and starts.
Where no console is attached (a pythonw start), the line a start ends on is also shown in a
message box. `--data-dir`, `--worker` and `--client-lockfile` are the
test overrides, loopback only, so a test run reads neither the profile, the Worker nor the client's files.
"""
import argparse
import os
import signal
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import alert, client, config, page, pairing, runfile, watcher, worker
from .autostart import Autostart

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
_MB_ICONINFORMATION = 0x40
_MB_SETFOREGROUND = 0x10000


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


def _serve(args, store, base, timeout, opener, stop, delay, beep, show, clock, sleep, autostart):
    # A quiet start is the system's, at logon: it never opens the browser, and a start that ends well shows no box.
    calm = (lambda line: None) if args.quiet else show
    run = runfile.RunFile(store.path.parent, clock=clock, sleep=sleep)
    try:
        holder = run.claim()
        if holder is not None and holder["closing"] and run.wait_released(holder, CLOSING_WAIT_SECONDS):
            holder = run.claim()  # the closing copy let its file go: this start claims it as a first one does
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
    try:
        state = pairing.PairingState(store, lambda secret: worker.check(secret, base=base, timeout=timeout))
        watch, alerter = _watcher(args, store, state, base, timeout, stop, delay, beep)
        # The page's quit sets the same stop event as Ctrl+C: the watcher, the page and the run file end below.
        pairing_page = page.PairingPage(state, watch=watch.snapshot, on_quit=stop.set, on_pause=watch.pause,
                                        on_resume=watch.resume, autostart=autostart, events=watch.events)
        url = pairing_page.start()
        watching = threading.Thread(target=watch.run, daemon=True)
        try:
            run.publish(pairing_page.port)
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
            try:  # first, so a start made while this one stops waits for it instead of opening a closing page
                run.mark_closing()
            except OSError:
                pass  # unmarked, a start meanwhile opens this page as before; the stop goes on
            if watching.is_alive():
                watching.join(STOP_SECONDS)
            alerter.flush(STOP_SECONDS)
            pairing_page.close()
    finally:
        run.release()
    return 0


def _break_as_interrupt():
    """Ctrl+Break stops the program as Ctrl+C does; only on Windows and only on the main thread."""
    if not hasattr(signal, "SIGBREAK") or threading.current_thread() is not threading.main_thread():
        return None
    return signal.signal(signal.SIGBREAK, signal.default_int_handler)


def main(argv=None, *, opener=webbrowser.open, stop=None, timeout=worker.TIMEOUT_SECONDS, delay=None,
         beep=alert.beep, box=_message_box, autostart=None, console=_console_attached, clock=time.monotonic,
         sleep=time.sleep):
    """`beep`, `box` and `autostart` are the seams of the sound, of the message box and of the start with Windows
    (the per-user Run value when None): a test run passes silent ones and a fake. `console` says whether a printed
    line reaches anybody; `clock` and `sleep` time the run file's re-reads."""
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
                          autostart if autostart is not None else Autostart())
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
