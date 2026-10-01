"""InstallAutostartTest: install.ps1 and uninstall.ps1 keep or remove an existing start with Windows.

Install never creates the per-user Run value; when it exists it is rewritten with this install's start line plus
--quiet, so a reinstall under another interpreter leaves no dead path. Uninstall removes it when it exists. Every
PowerShell run here names PENDIFY_RUN_KEY, a scratch key under HKCU\\Software\\pendify-test-<random> that the
case removes even when it fails, never the real Run key, in the private environment of InstallScriptTest.

A run of install.ps1 that is not dry and gets past pip reaches the shortcut step, which writes the person's real
shortcuts, so the install's rewrite is run alone: the script is loaded by its own parser up to the line that runs
the install, and its Run function is called against the scratch key. Uninstall's real run follows
InstallScriptTest's one real run: a fake interpreter, skipped when a real PendiFy shortcut exists.
"""
import os
import subprocess
import sys
import unittest
import uuid

import support
import test_install_script as scripts

autostart = support.module("autostart")

try:
    import winreg
except ImportError:  # not Windows
    winreg = None

NEEDS_REGISTRY = unittest.skipIf(scripts.POWERSHELL is None or winreg is None,
                                 "powershell.exe and winreg are both needed for a Run value")
SCRATCH_PREFIX = r"Software\pendify-test-"
OLD_LINE = r'"C:\an old folder\pythonw.exe" -m pendify --quiet'
# The script parsed by PowerShell itself and run up to the line that runs it, then one of its functions called.
LOADED = ("$t = $null; $e = $null; "
          "$ast = [System.Management.Automation.Language.Parser]::ParseFile('{path}', [ref]$t, [ref]$e); "
          "$block = $ast.Find({{ param($n) $n -is [System.Management.Automation.Language.ScriptBlockExpressionAst] }}, "
          "$true).ScriptBlock; $kept = @(); "
          "foreach ($s in $block.EndBlock.Statements) {{ if ($s.Extent.Text -like '$code = Invoke-*') {{ break }}; "
          "$kept += $s.Extent.Text }}; "
          "& ([scriptblock]::Create(($kept -join [Environment]::NewLine) + [Environment]::NewLine + '{call}'))")


@NEEDS_REGISTRY
class InstallAutostartTest(unittest.TestCase):
    setUp_private = scripts.InstallScriptTest.setUp
    private_env = scripts.InstallScriptTest.env

    def setUp(self):
        self.setUp_private()
        self.run_key = SCRATCH_PREFIX + uuid.uuid4().hex
        self.addCleanup(self.remove_scratch)

    def remove_scratch(self):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.run_key, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, "PendiFy")
        except FileNotFoundError:
            pass
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self.run_key)
        except FileNotFoundError:
            pass

    def env(self, dry=True, **extra):
        env = self.private_env(**extra)
        env["PENDIFY_RUN_KEY"] = self.run_key
        if not dry:
            del env["PENDIFY_DRYRUN"]
        return env

    def existing(self, text=OLD_LINE):
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.run_key) as key:
            winreg.SetValueEx(key, "PendiFy", 0, winreg.REG_SZ, text)

    def value(self):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.run_key) as key:
                return winreg.QueryValueEx(key, "PendiFy")[0]
        except FileNotFoundError:
            return None

    def key_exists(self):
        try:
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.run_key).Close()
            return True
        except FileNotFoundError:
            return False

    def loaded(self, script, call, env):
        command = LOADED.format(path=str(script), call=call.replace("'", "''"))
        return scripts._run(scripts.POWERSHELL, ["-ExecutionPolicy", "Bypass", "-Command", command], env)

    def test_both_heads_name_the_override_and_neither_script_holds_a_non_ascii_byte(self):
        # Mutation: an accented letter in the new message (Windows written with a quote mark). Red: a byte past 0x7F.
        for path in (scripts.INSTALL, scripts.UNINSTALL):
            with self.subTest(path=path.name):
                raw = path.read_bytes()
                self.assertEqual([i for i, byte in enumerate(raw) if byte > 0x7F], [])
                head = raw.decode("ascii").split("& {")[0]
                self.assertIn("PENDIFY_RUN_KEY", head)

    def test_the_install_dry_run_plans_the_rewrite_only_when_the_value_exists(self):
        # Mutation: the rewrite planned whether or not the value exists. Red: a plan line with no value.
        for extra in ({}, {"PENDIFY_NOSTART": "1"}):
            with self.subTest(extra=extra):
                done = scripts._file_form(self.env(**extra))
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                plan = scripts._plan(scripts._lines(done))
                self.assertEqual([line for line in plan if line.startswith("inicio con Windows")], [])
        self.assertFalse(self.key_exists())
        self.existing()
        done = scripts._file_form(self.env())
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        plan = scripts._plan(scripts._lines(done))
        self.assertEqual(len(plan), 7, plan)
        start = plan[6][len("iniciar: "):]
        self.assertEqual(plan[5], "inicio con Windows: " + start + " --quiet")
        self.assertTrue(plan[4].startswith("acceso directo: "), plan)
        # The installer's line is the one the page writes for the same interpreter.
        page_line = autostart.Autostart(registry=support.MemoryRegistry(), executable=sys.executable).command()
        self.assertEqual(start + " --quiet", page_line)
        self.assertEqual(self.value(), OLD_LINE)  # the dry run changed nothing
        done = scripts._file_form(self.env(PENDIFY_NOSTART="1"))
        self.assertEqual(scripts._plan(scripts._lines(done))[-1], plan[5])

    def test_the_uninstall_dry_run_plans_the_removal_only_when_the_value_exists(self):
        # Mutation: the removal planned whether or not the value exists. Red: a plan line with no value.
        done = scripts._file_form(self.env(), scripts.UNINSTALL)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual([line for line in scripts._plan(scripts._lines(done)) if "inicio con Windows" in line], [])
        self.existing()
        done = scripts._file_form(self.env(), scripts.UNINSTALL)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        removal = [line for line in scripts._plan(scripts._lines(done)) if "inicio con Windows" in line]
        self.assertEqual(removal, ["quitar inicio con Windows: HKCU:\\" + self.run_key + "\\PendiFy"])
        self.assertEqual(self.value(), OLD_LINE)

    def test_the_install_rewrites_an_existing_value_to_its_start_line_and_creates_none(self):
        # Mutation: the rewrite writing whether or not the value exists. Red: a value created under the scratch key.
        start = r'"C:\a new folder\pythonw.exe" -m pendify'
        call = "Update-RunValue '" + start + "'"
        for made_key in (False, True):
            with self.subTest(made_key=made_key):
                if made_key:
                    winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.run_key).Close()
                done = self.loaded(scripts.INSTALL, call, self.env(dry=False))
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertEqual(done.stderr, "")
                self.assertEqual((self.value(), self.key_exists()), (None, made_key))
        self.existing()
        done = self.loaded(scripts.INSTALL, call, self.env(dry=False))
        self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
        self.assertEqual(self.value(), start + " --quiet")
        self.assertEqual(scripts._lines(done), ["Inicio con Windows: " + start + " --quiet"])

    def test_the_uninstall_removes_an_existing_value_and_nothing_else(self):
        # Mutation: the removal skipped outside the dry run. Red: the value is still there.
        for present in (False, True):
            with self.subTest(present=present):
                if present:
                    self.existing()
                done = self.loaded(scripts.UNINSTALL, "$r = Remove-RunValue; Write-Host ('code ' + $r)",
                                   self.env(dry=False))
                self.assertEqual((done.returncode, done.stderr), (0, ""), done.stdout)
                removed = ["Inicio con Windows quitado: HKCU:\\" + self.run_key + "\\PendiFy"] if present else []
                self.assertEqual(scripts._lines(done), removed + ["code 0"])
                self.assertIsNone(self.value())
                self.assertEqual(self.key_exists(), present)

    def test_a_real_uninstall_run_removes_the_value_after_the_package(self):
        # Mutation: the removal left out of Invoke-Uninstall. Red: the value outlives the run.
        folders = [scripts._known_folder("Desktop"), scripts._known_folder("Programs")]
        if any((folder / "PendiFy.lnk").exists() for folder in folders):
            self.skipTest("a real PendiFy shortcut exists; this run is not dry and must not reach it")
        fake = self.tmp / "recorded" / "python.cmd"
        fake.parent.mkdir()
        fake.write_text("@echo off\r\nif \"%3\"==\"show\" (echo WARNING: Package not found: pendify& exit /b 1)\r\n"
                        "echo Successfully uninstalled pendify\r\nexit /b 0\r\n", encoding="ascii")
        self.record.parent.mkdir()
        self.record.write_text(str(fake), encoding="utf-8")
        self.existing()
        done = scripts._file_form(self.env(dry=False), scripts.UNINSTALL)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("Inicio con Windows quitado: HKCU:\\" + self.run_key + "\\PendiFy", scripts._lines(done))
        self.assertIsNone(self.value())


if __name__ == "__main__":
    unittest.main()
