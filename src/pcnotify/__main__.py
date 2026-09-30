"""The two commands.

    python -m <package>              the pairing page, opened in the default browser while unlinked
    python -m <package> ping <kind>  one alert with the stored pair, one fixed line per answer

`--data-dir` replaces %APPDATA% and `--worker` replaces the Worker's address with a loopback one, for
test runs. No watcher of any game client, no beep and no accept live here.
"""
import argparse
import sys
import threading
import webbrowser
from pathlib import Path

from . import config, page, pairing, worker

TICK_SECONDS = 1.0
REFUSED_LINE = "refused: the pairing of this PC was not accepted; link it again from the page"
NOT_LINKED_LINE = "not linked: start the program without arguments and link this PC first"
ALREADY_RUNNING_LINE = ""  # inert seam: the pins of item 6 and 8 are committed before the code


def _data_dir(value):
    if not value.strip():
        raise argparse.ArgumentTypeError("the data directory is empty")
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
    common.add_argument("--client-lockfile", default=argparse.SUPPRESS, help="inert seam")
    parser = argparse.ArgumentParser(prog=f"python -m {__package__}", parents=[common],
                                     description="Pairs this PC by a QR on a loopback page and sends alerts.")
    parser.add_argument("--dry", action="store_true", help="inert seam")
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


def _serve(store, base, timeout, opener, stop):
    state = pairing.PairingState(store, lambda secret: worker.check(secret, base=base, timeout=timeout))
    pairing_page = page.PairingPage(state)
    url = pairing_page.start()
    try:
        print(f"page: {url}", flush=True)
        if state.snapshot()["showCode"] and not opener(url):
            print("open the address above in a browser", flush=True)
        while True:
            state.tick()
            if stop.wait(TICK_SECONDS):
                break
    except KeyboardInterrupt:
        pass
    finally:
        pairing_page.close()
    return 0


def main(argv=None, *, opener=webbrowser.open, stop=None, timeout=worker.TIMEOUT_SECONDS, delay=None):
    args = _arguments(sys.argv[1:] if argv is None else argv)
    store = config.ConfigStore(getattr(args, "data_dir", None) or config.default_base_dir())
    base = getattr(args, "worker", worker.BASE_URL)
    if args.command == "ping":
        return _ping(store, base, args.kind, timeout)
    return _serve(store, base, timeout, opener, stop or threading.Event())


if __name__ == "__main__":
    sys.exit(main())
