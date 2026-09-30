# PC notifications, pairing core

A pure-Python program, standard library only, that pairs this PC with a person's Pendi account.
It keeps one secret of its own in a config file under the user's profile.
While it is not paired it shows that secret as a QR on a page served only on 127.0.0.1.
It asks the pairing service which link the phone made for that secret, then stores it.
With the pair stored it sends one of two alert kinds to that account.

## Commands

    python -m pcnotify
    python -m pcnotify ping <kind>

The first loads the config, starts the page and opens it in the default browser while the PC is not paired.
The second sends one alert with the stored pair and prints one line with the answer.

## Test runs

Both commands take `--data-dir <dir>`, used instead of `%APPDATA%` (the config file lives in a folder under it), and `--worker <address>`, used instead of the pairing service's address and accepted only as `http://127.0.0.1:<port>` or `http://localhost:<port>`, so a test run never reaches the real service.

    python -m unittest discover -s tests -v
