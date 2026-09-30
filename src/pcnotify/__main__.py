"""The commands.

    python -m <package>              the pairing page and the watcher of the game client, together
    python -m <package> --dry        the same, but the watcher never accepts: it alerts at the queue pop
    python -m <package> ping <kind>  one alert with the stored pair, one fixed line per answer

The page and the watcher stop together on Ctrl+C, on Ctrl+Break and on the page's quit button, which
removes the run file and exits 0. One instance per config folder: a second start opens the running page
and exits 0. `--data-dir`, `--worker` and `--client-lockfile` are the
test overrides, loopback only, so a test run reads neither the profile, the Worker nor the client's files.
"""
import argparse
import signal
import sys
import threading
import webbrowser
from pathlib import Path

from . import alert, client, config, page, pairing, runfile, watcher, worker

TICK_SECONDS = 1.0
STOP_SECONDS = 5.0
REFUSED_LINE = "refused: the pairing of this PC was not accepted; link it again from the page"
NOT_LINKED_LINE = "not linked: start the program without arguments and link this PC first"
ALREADY_RUNNING_LINE = "already running: opening the page of the program that runs"
PAGE_NOT_KNOWN_LINE = ("already running: the page of the program that runs is not known yet; start it again in a "
                       "moment to open it")
CLAIM_FAILED_LINE = "cannot start: the run file cannot be replaced: {path}"
FOLDER_FAILED_LINE = "cannot start: the config folder cannot be written: {path}"


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


def _serve(args, store, base, timeout, opener, stop, delay, beep):
    run = runfile.RunFile(store.path.parent)
    try:
        holder = run.claim()
    except OSError:
        print(CLAIM_FAILED_LINE, flush=True)
        return 1
    if holder is not None:
        print(ALREADY_RUNNING_LINE, flush=True)
        if holder["port"] is not None:
            opener(f"http://{page.ADDRESS}:{holder['port']}/")
        return 0
    try:
        state = pairing.PairingState(store, lambda secret: worker.check(secret, base=base, timeout=timeout))
        watch, alerter = _watcher(args, store, state, base, timeout, stop, delay, beep)
        # The page's quit sets the same stop event as Ctrl+C: the watcher, the page and the run file end below.
        pairing_page = page.PairingPage(state, watch=watch.snapshot, on_quit=stop.set)
        url = pairing_page.start()
        watching = threading.Thread(target=watch.run, daemon=True)
        try:
            run.publish(pairing_page.port)
            print(f"page: {url}", flush=True)
            if state.snapshot()["showCode"] and not opener(url):
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
         beep=alert.beep, box=None, console=None, clock=None, sleep=None):
    """`beep` is the one seam of the sound: a test run passes a silent one."""
    args = _arguments(sys.argv[1:] if argv is None else argv)
    store = config.ConfigStore(getattr(args, "data_dir", None) or config.default_base_dir())
    base = getattr(args, "worker", worker.BASE_URL)
    try:
        if args.command == "ping":
            return _ping(store, base, args.kind, timeout)
        previous = _break_as_interrupt()
        try:
            return _serve(args, store, base, timeout, opener, stop or threading.Event(),
                          delay or watcher.accept_delay, beep)
        finally:
            if previous is not None:
                signal.signal(signal.SIGBREAK, previous)
    except config.ConfigError as failure:  # at start or mid-run: its one sentence, never a traceback
        print(failure)
        return 1


if __name__ == "__main__":
    sys.exit(main())
