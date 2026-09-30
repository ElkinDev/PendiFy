# PC notifications

A pure-Python program, standard library only, that pairs this PC with a person's Pendi account.
It keeps one secret of its own in a config file under the user's profile.
While it is not paired it shows that secret as a QR on a page served only on 127.0.0.1.
It asks the pairing service which link the phone made for that secret, then stores it.
With the pair stored it sends one of two alert kinds to that account.

## Commands

    python -m pcnotify
    python -m pcnotify ping <kind>

The first loads the config, starts the page and opens it in the default browser while the PC is not paired.
It also watches the game client on this PC: when a match is found it accepts after a short random delay, beeps, and sends the found-match alert; when the match really starts it beeps and sends the started alert.
Add `--dry` to watch and alert without accepting. One copy runs per config folder: a second start opens the page of the one that runs and exits. Ctrl+C stops the page and the watcher together.
The second sends one alert with the stored pair and prints one line with the answer.

## Test runs

Both commands take `--data-dir <dir>`, used instead of `%APPDATA%` (the config file lives in a folder under it), and `--worker <address>`, used instead of the pairing service's address and accepted only as `http://127.0.0.1:<port>` or `http://localhost:<port>`, so a test run never reaches the real service. `--client-lockfile <file>` replaces the game client's lockfile, with no read of the running processes, and the client it names is reached over plain http on 127.0.0.1, so a test run never reaches a real client.

    python -m unittest discover -s tests -v
