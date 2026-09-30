"""ClientCredentialsTest: where the game client's port and token come from (S:693-716), with no psutil.

Every lockfile lives in a temp folder under build/tmp and the process read is a fake: no test reads the
client's real lockfile paths and no test starts a shell.
"""
import subprocess
import unittest
from pathlib import Path

import support
from support import FakeClock

client = support.module("client")

# Test vectors, never a real client's values.
TOKEN = "fake-Token_0123"
PORT = 51234
# A command line in the shape the client's process carries: two decoys that name a port and a token too.
COMMAND_LINE = (f'"C:/Games/Client/ClientUx.exe" --riotclient-app-port=5555 --riotclient-auth-token=decoy '
                f"--app-port={PORT} --remoting-auth-token={TOKEN} --app-pid=4242")


class FakeRun:
    """subprocess.run as the reader calls it: records each call, answers with `result` or raises it."""

    def __init__(self, result):
        self.result, self.calls = result, []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def completed(stdout, code=0):
    return subprocess.CompletedProcess(["powershell"], code, stdout=stdout, stderr="")


class ClientCredentialsTest(unittest.TestCase):
    def setUp(self):
        tmp = support.temp_dir()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        self.first, self.second = self.folder / "first" / "lockfile", self.folder / "second" / "lockfile"
        self.clock = FakeClock()

    def reader(self, run=None):
        return client.ClientCredentials((str(self.first), str(self.second)), run=run or FakeRun(completed("")),
                                        clock=self.clock)

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def values(self, found):
        return None if found is None else (found.port, found.token)

    def test_a_lockfile_gives_its_third_field_as_port_and_its_fourth_as_token(self):
        # Mutation: the fourth field taken as the port. Red: the token read as the port, or no client.
        run = FakeRun(completed(COMMAND_LINE))
        self.write(self.second, f"LeagueClient:4242:{PORT}:{TOKEN}:https")
        self.assertEqual(self.values(self.reader(run).read()), (PORT, TOKEN))
        self.write(self.first, f"LeagueClient:77:{PORT + 1}:other:https")
        self.assertEqual(self.values(self.reader(run).read()), (PORT + 1, "other"))
        self.assertEqual(run.calls, [])
        self.assertNotIn(TOKEN, repr(self.reader(run).read()) + repr(client.Credentials(PORT, TOKEN)))

    def test_a_missing_empty_three_field_or_unreadable_file_gives_none(self):
        # Mutation: `len(data) >= 3` accepted as a lockfile. Red: the three-field file gives a client.
        self.write(self.first, f"LeagueClient:4242:{PORT}:{TOKEN}:https")
        self.assertEqual(self.values(self.reader().read()), (PORT, TOKEN))
        cases = {"missing": None, "empty": "", "three fields": f"LeagueClient:4242:{PORT}",
                 "a port that is no number": f"LeagueClient:4242:port:{TOKEN}:https",
                 "a port out of range": f"LeagueClient:4242:70000:{TOKEN}:https", "unreadable": "folder"}
        for name, text in cases.items():
            with self.subTest(case=name):
                for path in (self.first, self.second):
                    if path.is_dir():
                        path.rmdir()
                    elif path.exists():
                        path.unlink()
                if text == "folder":
                    self.first.mkdir(parents=True)  # a folder where the file should be: open() refuses it
                elif text is not None:
                    self.write(self.first, text)
                self.assertIsNone(self.reader().read())

    def test_the_process_read_is_asked_only_when_no_lockfile_answers_and_not_twice_inside_10_s(self):
        # Mutation: the 10 s spacing removed. Red: a second shell inside 10 s of the injected clock.
        run = FakeRun(completed(COMMAND_LINE))
        reader = self.reader(run)
        self.write(self.first, f"LeagueClient:4242:{PORT}:{TOKEN}:https")
        self.assertEqual(self.values(reader.read()), (PORT, TOKEN))
        self.assertEqual(run.calls, [])
        self.first.unlink()
        self.assertEqual(self.values(reader.read()), (PORT, TOKEN))
        self.assertEqual(len(run.calls), 1)
        for advance in (5.0, 4.99):
            self.clock.advance(advance)
            self.assertIsNone(reader.read())
        self.assertEqual(len(run.calls), 1)
        self.clock.advance(0.02)
        self.assertEqual(self.values(reader.read()), (PORT, TOKEN))
        self.assertEqual(len(run.calls), 2)
        args, kwargs = run.calls[0]
        self.assertEqual(args[:3], ["powershell", "-NoProfile", "-Command"])
        self.assertEqual(len(args), 4)
        for word in ("Get-CimInstance", "Win32_Process", "LeagueClient", "CommandLine"):
            self.assertIn(word, args[3])
        self.assertEqual((kwargs.get("timeout"), kwargs.get("capture_output"), kwargs.get("text")), (5, True, True))
        self.assertIsNone(client.ClientCredentials((str(self.first),), run=None, clock=self.clock).read())

    def test_the_process_output_is_parsed_by_the_script_s_two_expressions(self):
        # Mutation: the token expression without its dash. Red: the token cut at the first dash.
        output = "\r\n".join(["", '"C:/Games/Client/Client.exe" --app-port=6000 --no-token-here',
                              COMMAND_LINE, "--app-port=7000 --remoting-auth-token=later", ""])
        self.assertEqual(self.values(self.reader(FakeRun(completed(output))).read()), (PORT, TOKEN))
        self.assertIsNone(self.reader(FakeRun(completed("--app-port=6000\r\n--remoting-auth-token=x\r\n"))).read())

    def test_a_timeout_a_nonzero_exit_or_a_missing_shell_gives_none(self):
        # Mutation: the exit code not read. Red: a failed shell's output taken as a client.
        self.assertEqual(self.values(self.reader(FakeRun(completed(COMMAND_LINE))).read()), (PORT, TOKEN))
        for name, result in (("timeout", subprocess.TimeoutExpired(["powershell"], 5)),
                             ("nonzero exit", completed(COMMAND_LINE, code=1)),
                             ("no shell", FileNotFoundError(2, "not found")),
                             ("refused", PermissionError(13, "denied"))):
            with self.subTest(case=name):
                self.assertIsNone(self.reader(FakeRun(result)).read())


if __name__ == "__main__":
    unittest.main()
