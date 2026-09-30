"""Pins for install.ps1, uninstall.ps1 and the README install line.

Every PowerShell run here is a dry run (PCNOTIFY_DRYRUN=1) with a PATH, a LOCALAPPDATA and an APPDATA
the test builds, so nothing is installed, started or written outside a temp directory. The one run
that is not dry is the uninstaller against a fake interpreter that answers the package is still
there, which stops before the shortcut step, and it is skipped when a real pcnotify shortcut exists.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALL = ROOT / "install.ps1"
UNINSTALL = ROOT / "uninstall.ps1"
README = ROOT / "README.md"

DEFAULT_SOURCE = "https://github.com/ElkinDev/pcnotify/archive/refs/heads/main.zip"
INSTALL_LINE = "irm https://raw.githubusercontent.com/ElkinDev/pcnotify/main/install.ps1 | iex"
UNINSTALL_LINE = "irm https://raw.githubusercontent.com/ElkinDev/pcnotify/main/uninstall.ps1 | iex"
ALLOWED_HOSTS = {"github.com", "raw.githubusercontent.com", "www.python.org"}
WINGET_LINE = (
    "winget install --id Python.Python.3.13 -e --scope user --silent "
    "--accept-package-agreements --accept-source-agreements"
)
PLAN = "[plan] "
FOUND = "Python: "
STUB = "rem the Microsoft Store alias stub prints nothing"


def _powershell():
    root = os.environ.get("SystemRoot", r"C:\Windows")
    path = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(path) if path.is_file() else None


POWERSHELL = _powershell()
PWSH = shutil.which("pwsh")
NEEDS_POWERSHELL = unittest.skipIf(POWERSHELL is None, "powershell.exe is absent on this machine")


def _run(exe, args, env=None):
    return subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", *args],
        cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )


def _file_form(env, script=INSTALL):
    return _run(POWERSHELL, ["-ExecutionPolicy", "Bypass", "-File", str(script)], env)


def _piped_form(env, script=INSTALL):
    command = ("Get-Content -Raw -LiteralPath '" + str(script) + "' | Invoke-Expression; "
               "exit $LASTEXITCODE")
    return _run(POWERSHELL, ["-ExecutionPolicy", "Bypass", "-Command", command], env)


def _known_folder(name):
    done = _run(POWERSHELL, ["-Command", "[Environment]::GetFolderPath('" + name + "')"])
    return Path(done.stdout.strip())


def _lines(done):
    return [line.rstrip() for line in done.stdout.splitlines() if line.strip()]


def _plan(lines):
    return [line[len(PLAN):] for line in lines if line.startswith(PLAN)]


def _found(lines):
    return [line[len(FOUND):] for line in lines if line.startswith(FOUND)]


class InstallScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pcnotify-install-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.fakes = self.tmp / "fakes"
        self.local = self.tmp / "localappdata"
        self.appdata = self.tmp / "appdata"
        self.record = self.appdata / "pcnotify" / "python.txt"
        self.fakes.mkdir()
        self.local.mkdir()
        self.appdata.mkdir()

    def env(self, with_real_python=True, **extra):
        self.assertTrue(INSTALL.is_file(), "install.ps1 is missing at the repository root")
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PCNOTIFY_")}
        entries = [str(self.fakes)]
        if with_real_python:
            entries.append(str(Path(sys.executable).parent))
        env["PATH"] = os.pathsep.join(entries)
        env["LOCALAPPDATA"] = str(self.local)
        env["APPDATA"] = str(self.appdata)
        env["PCNOTIFY_DRYRUN"] = "1"
        env.update(extra)
        return env

    def fake(self, name, body):
        (self.fakes / name).write_text("@echo off\r\n" + body + "\r\n", encoding="ascii")

    def test_scripts_are_ascii_with_no_forbidden_token_and_only_known_hosts(self):
        # Mutation: an accented letter in a message (encontro written with an accent), red.
        for path in (INSTALL, UNINSTALL):
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file(), path.name + " is missing")
                raw = path.read_bytes()
                bad = [i for i, byte in enumerate(raw) if byte > 0x7F]
                self.assertEqual(bad, [], path.name + " holds non-ASCII bytes at these offsets")
                text = raw.decode("ascii")
                for token in (r"Set-ExecutionPolicy", r"RunAs", r"--scope\s+machine",
                              r"Invoke-Expression", r"\biex\b"):
                    self.assertIsNone(re.search(token, text, re.IGNORECASE), token)
                hosts = re.findall(r"https?://([^/\s'\"`)]+)", text)
                if path == INSTALL:
                    self.assertTrue(hosts, "install.ps1 names no address at all")
                self.assertEqual(sorted(set(hosts) - ALLOWED_HOSTS), [])

    @NEEDS_POWERSHELL
    def test_both_files_parse_under_windows_powershell(self):
        # Mutation: an unclosed brace in install.ps1, red.
        self._assert_parses(POWERSHELL)

    @unittest.skipIf(PWSH is None, "pwsh (PowerShell 7) is not on this machine")
    def test_both_files_parse_under_pwsh(self):
        # Mutation: syntax only Windows PowerShell accepts, red.
        self._assert_parses(PWSH)

    def _assert_parses(self, exe):
        for path in (INSTALL, UNINSTALL):
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file(), path.name + " is missing")
                command = (
                    "$t = $null; $e = $null; "
                    "[void][System.Management.Automation.Language.Parser]::ParseFile('"
                    + str(path) + "', [ref]$t, [ref]$e); "
                    "$e | ForEach-Object { $_.ToString() }; exit $e.Count"
                )
                done = _run(exe, ["-Command", command])
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_readme_holds_the_install_line_once_per_language_and_no_em_dash(self):
        # Mutation: the install line dropped from the English section, red.
        text = README.read_text(encoding="utf-8")
        self.assertNotIn("\u2014", text)
        spanish = re.search(r"^## Espa\S*ol\s*$(.*?)^## English\s*$", text, re.M | re.S)
        english = re.search(r"^## English\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
        self.assertIsNotNone(spanish, "no Spanish section before the English one")
        self.assertIsNotNone(english, "no English section")
        self.assertEqual(spanish.group(1).count(INSTALL_LINE), 1)
        self.assertEqual(english.group(1).count(INSTALL_LINE), 1)
        self.assertEqual(text.count(INSTALL_LINE), 2)

    def test_readme_holds_the_uninstall_line_once_per_language(self):
        # Mutation: the Spanish uninstall road kept as a downloaded file run with -File, red.
        text = README.read_text(encoding="utf-8")
        spanish = re.search(r"^## Espa\S*ol\s*$(.*?)^## English\s*$", text, re.M | re.S)
        english = re.search(r"^## English\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
        self.assertEqual(spanish.group(1).count(UNINSTALL_LINE), 1)
        self.assertEqual(english.group(1).count(UNINSTALL_LINE), 1)
        self.assertEqual(text.count(UNINSTALL_LINE), 2)

    @NEEDS_POWERSHELL
    def test_dry_run_as_a_file_prints_the_plan_in_order(self):
        # Mutation: PCNOTIFY_SOURCE ignored (the default source always used), red; the python.txt
        # plan line dropped, red.
        desktop, programs = _known_folder("Desktop"), _known_folder("Programs")
        done = _file_form(self.env())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = _lines(done)
        found = _found(lines)
        self.assertEqual(len(found), 1, lines)
        self.assertEqual(os.path.normcase(found[0].split(" (")[0]),
                         os.path.normcase(sys.executable))
        plan = _plan(lines)
        self.assertEqual(len(plan), 5, plan)
        self.assertTrue(plan[0].endswith(
            " -m pip install --user --upgrade --no-warn-script-location " + DEFAULT_SOURCE), plan[0])
        self.assertEqual(plan[1], "anotar Python en " + str(self.record))
        self.assertEqual(plan[2], "acceso directo: " + str(desktop / "pcnotify.lnk"))
        self.assertEqual(plan[3], "acceso directo: " + str(programs / "pcnotify.lnk"))
        self.assertTrue(plan[4].startswith("iniciar: ") and plan[4].endswith(" -m pcnotify"), plan[4])
        self.assertLess(lines.index(FOUND + found[0]), lines.index(PLAN + plan[0]))

        source = r"C:\some folder\pcnotify-main.zip"
        done = _file_form(self.env(PCNOTIFY_SOURCE=source))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        pip = _plan(_lines(done))[0]
        self.assertIn(source, pip)
        self.assertNotIn(DEFAULT_SOURCE, pip)

        done = _file_form(self.env(PCNOTIFY_NOSTART="1"))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        plan = _plan(_lines(done))
        self.assertEqual(len(plan), 4, plan)
        self.assertFalse([line for line in plan if line.startswith("iniciar: ")])

    @NEEDS_POWERSHELL
    def test_dry_run_piped_to_invoke_expression_gives_the_same_plan(self):
        # Mutation: a param() block or a top-level return in install.ps1, red.
        for extra in ({}, {"PCNOTIFY_NOSTART": "1"}):
            with self.subTest(extra=extra):
                as_file = _file_form(self.env(**extra))
                piped = _piped_form(self.env(**extra))
                self.assertEqual(as_file.returncode, 0, as_file.stdout + as_file.stderr)
                self.assertEqual(piped.returncode, 0, piped.stdout + piped.stderr)
                self.assertTrue(_plan(_lines(piped)), piped.stdout + piped.stderr)
                self.assertEqual(_lines(piped), _lines(as_file))

    @NEEDS_POWERSHELL
    def test_no_python_names_winget_when_present_and_python_org_when_not(self):
        # Mutation: the Store stub accepted (a candidate that prints nothing counts), red.
        self.fake("python.cmd", STUB)
        self.fake("winget.cmd", "echo fake winget must never run & exit /b 7")
        done = _file_form(self.env(with_real_python=False))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = _lines(done)
        self.assertFalse(_found(lines), lines)
        plan = _plan(lines)
        self.assertEqual(plan[0], WINGET_LINE, lines)
        self.assertNotIn("fake winget must never run", done.stdout)
        expected = self.local / "Programs" / "Python" / "Python313" / "python.exe"
        self.assertTrue(plan[1].startswith(str(expected) + " -m pip install "), plan[1])

        (self.fakes / "winget.cmd").unlink()
        for form in (_file_form, _piped_form):
            with self.subTest(form=form.__name__):
                done = form(self.env(with_real_python=False))
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertIn("https://www.python.org/downloads/", done.stdout)
                self.assertFalse(_plan(_lines(done)), done.stdout)

    @NEEDS_POWERSHELL
    def test_localappdata_python_is_found_newest_first_after_the_stub(self):
        # Mutation: the LOCALAPPDATA folders tried oldest first, red.
        import _winapi

        self.fake("python.cmd", STUB)
        base = self.local / "Programs" / "Python"
        base.mkdir(parents=True)
        real = str(Path(sys.executable).parent)
        for name in ("Python39", "Python311", "Python310"):
            link = base / name
            _winapi.CreateJunction(real, str(link))
            self.addCleanup(os.rmdir, str(link))
        done = _file_form(self.env(with_real_python=False))
        found = _found(_lines(done))
        self.assertEqual(len(found), 1, done.stdout + done.stderr)
        self.assertIn(os.path.normcase(str(base / "Python311")), os.path.normcase(found[0]))

    @NEEDS_POWERSHELL
    def test_dry_run_writes_no_shortcut_and_no_file(self):
        # Mutation: the python.txt write not guarded by the dry run, red. (The shortcut step unguarded
        # reds the snapshot too, but would write a real shortcut, so it is not the named mutation.)
        folders = [_known_folder("Desktop"), _known_folder("Programs")]
        before = [{p.name for p in folder.iterdir()} for folder in folders]
        done = _file_form(self.env())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        after = [{p.name for p in folder.iterdir()} for folder in folders]
        self.assertEqual([sorted(a - b) for a, b in zip(after, before)], [[], []])
        self.assertFalse((self.local / "Programs").exists())
        self.assertEqual(list(self.appdata.iterdir()), [])

    @NEEDS_POWERSHELL
    def test_a_banner_before_the_version_does_not_hide_a_working_python(self):
        # Mutation: index 0 restored (only the first output line read as the version), red.
        self.fake("python.cmd", "echo Welcome, this Python prints a banner first\r\n\"" + sys.executable + "\" %*")
        done = _file_form(self.env(with_real_python=False))
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        found = _found(_lines(done))
        self.assertEqual(len(found), 1, done.stdout + done.stderr)
        self.assertEqual(os.path.normcase(found[0].split(" (")[0]), os.path.normcase(sys.executable))

    @NEEDS_POWERSHELL
    def test_a_python_inside_a_virtual_environment_is_skipped_with_its_plan_line(self):
        # Mutation: the sys.prefix against sys.base_prefix check removed, red.
        venv = self.tmp / "venv"
        made = subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(made.returncode, 0, made.stdout + made.stderr)
        scripts = venv / "Scripts"
        env = self.env()
        env["PATH"] = os.pathsep.join([str(scripts), env["PATH"]])
        done = _file_form(env)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = _lines(done)
        skipped = [line for line in _plan(lines) if line.startswith("se omite ")]
        self.assertEqual(len(skipped), 1, lines)
        self.assertIn(os.path.normcase(str(scripts)), os.path.normcase(skipped[0]))
        self.assertTrue(skipped[0].endswith(": es un entorno virtual"), skipped[0])
        found = _found(lines)
        self.assertEqual(os.path.normcase(found[0].split(" (")[0]), os.path.normcase(sys.executable))

    def recorded_python(self):
        recorded = self.tmp / "recorded" / "python.exe"
        recorded.parent.mkdir()
        recorded.write_bytes(b"")
        self.record.parent.mkdir()
        self.record.write_text(str(recorded), encoding="utf-8")
        return recorded

    @NEEDS_POWERSHELL
    def test_uninstall_dry_run_plans_the_recorded_python_and_probes_without_it(self):
        # Mutation: python.txt ignored (the uninstaller always probes), red.
        uninstall = "-m pip uninstall -y pcnotify"
        done = _file_form(self.env(), UNINSTALL)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = _lines(done)
        self.assertEqual(os.path.normcase(_found(lines)[0].split(" (")[0]), os.path.normcase(sys.executable))
        self.assertEqual(_plan(lines)[0], sys.executable + " " + uninstall)

        recorded = self.recorded_python()
        done = _file_form(self.env(), UNINSTALL)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        lines = _lines(done)
        self.assertEqual(_found(lines), [str(recorded) + " (anotado en " + str(self.record) + ")"])
        plan = _plan(lines)
        self.assertEqual(plan[0], str(recorded) + " " + uninstall)
        self.assertEqual(plan[1], "comprobar: " + str(recorded) + " -m pip show pcnotify")

    @NEEDS_POWERSHELL
    def test_uninstall_dry_run_piped_to_invoke_expression_gives_the_same_plan(self):
        # Mutation: a param() block or a top-level exit in uninstall.ps1, red.
        for with_record in (False, True):
            with self.subTest(with_record=with_record):
                if with_record:
                    self.recorded_python()
                as_file = _file_form(self.env(), UNINSTALL)
                piped = _piped_form(self.env(), UNINSTALL)
                self.assertEqual(as_file.returncode, 0, as_file.stdout + as_file.stderr)
                self.assertEqual(piped.returncode, 0, piped.stdout + piped.stderr)
                self.assertTrue(_plan(_lines(piped)), piped.stdout + piped.stderr)
                self.assertEqual(_lines(piped), _lines(as_file))

    @NEEDS_POWERSHELL
    def test_uninstall_reports_a_package_pip_left_in_place_and_exits_1(self):
        # Mutation: the pip show check after pip uninstall removed (the removal assumed), red.
        folders = [_known_folder("Desktop"), _known_folder("Programs")]
        if any((folder / "pcnotify.lnk").exists() for folder in folders):
            self.skipTest("a real pcnotify shortcut exists; this run is not dry and must not reach it")
        fake = self.tmp / "recorded" / "python.cmd"
        fake.parent.mkdir()
        fake.write_text("@echo off\r\nif \"%3\"==\"show\" (echo Name: pcnotify& exit /b 0)\r\n"
                        "echo WARNING: Skipping pcnotify as it is not installed.\r\nexit /b 0\r\n",
                        encoding="ascii")
        self.record.parent.mkdir()
        self.record.write_text(str(fake), encoding="utf-8")
        env = self.env()
        del env["PCNOTIFY_DRYRUN"]
        done = _file_form(env, UNINSTALL)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        lines = _lines(done)
        self.assertTrue(lines[-1].startswith("pcnotify sigue instalado en " + str(fake)), lines)
        self.assertNotIn("Paquete pcnotify quitado.", lines)

if __name__ == "__main__":
    unittest.main()
