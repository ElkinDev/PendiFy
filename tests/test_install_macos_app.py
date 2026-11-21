"""Pins for install.sh's app step: the signed app a release names is downloaded and checked, else built on the Mac.

tools/release-macos-app.sh publishes PendiFy.app signed with the owner's Developer ID and notarized, and writes its
URL and sha256 into install.sh. install.sh keeps that download only when its sha256, its signature and its team all
check; otherwise, or with PENDIFY_BUILD_APP=1, it builds the app on the Mac and says why on one line.

Every run here is a copy of install.sh with those two lines filled in by the test, on macOS only, under a HOME the test
builds: the environment there is a stand-in whose pip does nothing and whose Python finds the package in src/, so the
app lands in the temporary HOME's Applications. PENDIFY_SIGN_IDENTITY=- signs an app built there ad hoc, so the
keychain is never read; PENDIFY_LSREGISTER names a stand-in that records its call, so LaunchServices learns no
temporary app; PENDIFY_NOSTART=1 opens nothing, and that HOME holds no LaunchAgent. The zip a run downloads is made by
the test and named by a file:// URL: an app signed ad hoc that claims a team with codesign's --team-identifier, which
is all that codesign --verify and the team read look at. The pin on the lines themselves runs on every system.
"""
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import support

INSTALL = support.ROOT / "install.sh"
LAUNCHER = support.package_dir() / "macos" / "PendiFy"
# The owner's team: a downloaded app is kept only when it is signed by this team.
TEAM = "BL4KZH338B"
OTHER_TEAM = "ZZZZ999999"
BUNDLE_ID = "io.github.elkindev.pendify.app"
# What tools/release-macos-app.sh writes: the release asset of the repository, and the zip's sha256.
RELEASE_ASSET = re.compile(r"https://github\.com/ElkinDev/PendiFy/releases/download/"
                           r"macos-app-([0-9][0-9.]*)/PendiFy-macos-\1\.zip")
SHA256 = re.compile(r"[0-9a-f]{64}")
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
# A file only the published app holds, to tell it from the app built here.
MARK = "from-the-release"
DOWNLOADED = "Downloaded the app signed by team " + TEAM + "."
NOT_PUBLISHED = "Building the app here: no signed app is published yet."
FORCED = "Building the app here: PENDIFY_BUILD_APP=1 asks for it."
NOT_USED = "Building the app here: the signed download was not used, "


def published_lines(text):
    """The values of install.sh's MACOS_APP_URL, MACOS_APP_SHA256 and MACOS_APP_TEAM lines, by name, with how often
    each name starts a line."""
    lines = text.splitlines()
    found = {}
    for name in ("MACOS_APP_URL", "MACOS_APP_SHA256", "MACOS_APP_TEAM"):
        starts = [line for line in lines if line.startswith(name + "=")]
        values = [match.group(1) for match in (re.fullmatch(name + r'="([^"]*)"', line) for line in starts) if match]
        found[name] = (len(starts), values)
    return found


class PublishedAppLinesTest(unittest.TestCase):
    def test_the_published_pair_is_both_empty_or_a_release_asset_and_its_sha256_and_the_team_is_the_owners(self):
        # Mutation: only the URL committed after a release. Red: the pair is half filled in.
        # Mutation: the expected team changed. Red: it is not the owner's.
        found = published_lines(INSTALL.read_text(encoding="utf-8"))
        for name, (count, values) in found.items():
            self.assertEqual((count, len(values)), (1, 1), name + " is not one quoted line")
        url, sha, team = (found[name][1][0] for name in ("MACOS_APP_URL", "MACOS_APP_SHA256", "MACOS_APP_TEAM"))
        self.assertEqual(team, TEAM)
        if url or sha:
            self.assertRegex(url, r"\A" + RELEASE_ASSET.pattern + r"\Z")
            self.assertRegex(sha, r"\A" + SHA256.pattern + r"\Z")


@unittest.skipUnless(sys.platform == "darwin", "macOS only: install.sh, codesign and ditto")
@unittest.skipUnless(sys.version_info >= (3, 10), "the installer takes Python 3.10 or newer, and its stand-in "
                                                  "environment runs this interpreter")
class InstallMacAppTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pendify-macapp-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home, self.fakes, self.temp = self.tmp / "home", self.tmp / "fakes", self.tmp / "temp"
        self.record = self.tmp / "lsregister.txt"
        self.app = self.home / "Applications" / "PendiFy.app"
        venv_bin = self.home / "Library" / "Application Support" / "pendify" / "venv" / "bin"
        for folder in (self.fakes, self.temp, venv_bin):
            folder.mkdir(parents=True)
        self.assertTrue(self.app.is_relative_to(self.tmp), self.app)
        python = shlex.quote(sys.executable)
        # The environment's Python: its pip installs nothing, and it imports the package from src/.
        self.executable(venv_bin / "python", 'if [ "$1" = -m ] && [ "$2" = pip ]; then exit 0; fi\n'
                        "PYTHONPATH=" + shlex.quote(str(support.SRC)) + " exec " + python + ' "$@"')
        # A Python on PATH, so the installer goes on; with the environment in place it makes none.
        self.executable(self.fakes / "python3", "exec " + python + ' "$@"')
        self.executable(self.fakes / "lsregister", 'printf "%s\\n" "$*" >> ' + shlex.quote(str(self.record)))
        self.scripts = 0

    @staticmethod
    def executable(path, body):
        path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
        path.chmod(0o755)

    def tool(self, *args):
        done = subprocess.run(args, capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, " ".join(args) + "\n" + done.stdout + done.stderr)
        return done

    def released(self, url, sha):
        """A copy of install.sh with the release's two lines set, written as tools/release-macos-app.sh writes them."""
        text = INSTALL.read_text(encoding="utf-8")
        for name, value in (("MACOS_APP_URL", url), ("MACOS_APP_SHA256", sha)):
            text, count = re.subn("^" + name + "=.*$", lambda _: name + '="' + value + '"', text, flags=re.M)
            self.assertEqual(count, 1, name)
        self.scripts += 1
        path = self.tmp / ("install-%d.sh" % self.scripts)
        path.write_text(text, encoding="utf-8")
        return path

    def zipped(self, team=TEAM, broken=False):
        """A zip of PendiFy.app holding the mark, signed ad hoc as `team`; `broken` changes the mark after the
        signing, so the signature no longer verifies. Answers its file:// URL and its sha256."""
        folder = Path(tempfile.mkdtemp(prefix="release-", dir=self.tmp))
        app = folder / "PendiFy.app"
        (app / "Contents" / "MacOS").mkdir(parents=True)
        (app / "Contents" / "Resources").mkdir()
        shutil.copy(LAUNCHER, app / "Contents" / "MacOS" / "PendiFy")
        # The Info.plist install.sh writes, which tools/release-macos-app.sh reads from it.
        plist = re.search(r'Contents/Info\.plist" <<EOF\n(.*?\n)EOF\n', INSTALL.read_text(encoding="utf-8"), re.S)
        self.assertIsNotNone(plist, "no Info.plist in install.sh")
        (app / "Contents" / "Info.plist").write_text(plist.group(1), encoding="utf-8")
        mark = app / "Contents" / "Resources" / MARK
        mark.write_text("the release\n", encoding="utf-8")
        signed = subprocess.run(["codesign", "--force", "--sign", "-", "--team-identifier", team, "--identifier",
                                 BUNDLE_ID, str(app)], capture_output=True, text=True, timeout=60)
        if signed.returncode != 0:
            self.skipTest("this codesign cannot name a team on an ad hoc signature: " + signed.stderr.strip())
        if broken:
            mark.write_text("changed after the signing\n", encoding="utf-8")
        archive = folder / "PendiFy.zip"
        self.tool("ditto", "-c", "-k", "--keepParent", str(app), str(archive))
        return archive.as_uri(), hashlib.sha256(archive.read_bytes()).hexdigest()

    def install(self, script, **extra):
        """install.sh run under the test's HOME; its non-empty output lines."""
        self.record.unlink(missing_ok=True)
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(("PENDIFY_", "PYTHON"))}
        env.update(HOME=str(self.home), PATH=str(self.fakes) + ":" + SYSTEM_PATH, TMPDIR=str(self.temp),
                   PENDIFY_NOSTART="1", PENDIFY_SIGN_IDENTITY="-", PENDIFY_LSREGISTER=str(self.fakes / "lsregister"))
        env.update(extra)
        done = subprocess.run(["/bin/bash", str(script)], cwd=str(self.tmp), env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=180)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return [line.rstrip() for line in done.stdout.splitlines() if line.strip()]

    def app_line(self, lines):
        """The line after "Making <app>": which way the app was made, and why."""
        return lines[lines.index("Making " + str(self.app)) + 1]

    def team_of(self, app):
        shown = self.tool("codesign", "-dv", str(app)).stderr
        return re.search(r"^TeamIdentifier=(.*)$", shown, re.M).group(1)

    def assert_in_place(self, downloaded):
        """The app at its place, from the release or built here, registered once and no download left behind."""
        self.assertTrue(os.access(self.app / "Contents" / "MacOS" / "PendiFy", os.X_OK), "no launcher in the app")
        self.assertEqual((self.app / "Contents" / "Resources" / MARK).is_file(), downloaded)
        # The app built here is signed ad hoc (PENDIFY_SIGN_IDENTITY=-), which names no team.
        self.assertEqual(self.team_of(self.app), TEAM if downloaded else "not set")
        self.tool("codesign", "--verify", "--deep", "--strict", str(self.app))
        self.assertEqual(self.record.read_text(encoding="utf-8"), "-f " + str(self.app) + "\n")
        self.assertEqual(list(self.temp.iterdir()), [])

    def test_a_published_app_that_passes_every_check_is_put_in_place_and_registered(self):
        # Mutation: the download route never taken. Red: the app is built here, with no mark and no team.
        lines = self.install(self.released(*self.zipped()))
        self.assertEqual(self.app_line(lines), DOWNLOADED)
        self.assert_in_place(downloaded=True)

    def test_a_sha256_mismatch_builds_the_app_here(self):
        # Mutation: the sha256 check dropped. Red: the zip is kept although its sha256 is not the published one.
        url, _ = self.zipped()
        lines = self.install(self.released(url, "0" * 64))
        self.assertEqual(self.app_line(lines), NOT_USED + "its sha256 is not the one expected.")
        self.assert_in_place(downloaded=False)

    def test_a_team_mismatch_builds_the_app_here(self):
        # Mutation: the team check dropped. Red: an app signed by another team is kept.
        lines = self.install(self.released(*self.zipped(team=OTHER_TEAM)))
        self.assertEqual(self.app_line(lines),
                         NOT_USED + "its team is not " + TEAM + " (codesign reads: " + OTHER_TEAM + ").")
        self.assert_in_place(downloaded=False)

    def test_a_failed_download_or_a_broken_signature_builds_the_app_here(self):
        # Mutation: the signature check dropped. Red: an app changed after its signing is kept.
        url, sha = self.zipped(broken=True)
        cases = (((url, sha), "its signature does not verify."),
                 (((self.tmp / "absent.zip").as_uri(), sha), "the download failed."))
        for (url, sha), why in cases:
            with self.subTest(why=why):
                lines = self.install(self.released(url, sha))
                self.assertEqual(self.app_line(lines), NOT_USED + why)
                self.assert_in_place(downloaded=False)

    def test_with_no_published_app_the_app_is_built_here(self):
        # Mutation: a download tried with an empty URL. Red: the line says the download failed.
        for url, sha in (("", ""), (self.zipped()[0], "")):
            with self.subTest(url=url, sha=sha):
                lines = self.install(self.released(url, sha))
                self.assertEqual(self.app_line(lines), NOT_PUBLISHED)
                self.assert_in_place(downloaded=False)

    def test_pendify_build_app_builds_it_here_even_with_an_app_published(self):
        # Mutation: PENDIFY_BUILD_APP ignored. Red: the published app is downloaded.
        lines = self.install(self.released(*self.zipped()), PENDIFY_BUILD_APP="1")
        self.assertEqual(self.app_line(lines), FORCED)
        self.assert_in_place(downloaded=False)

    def test_the_dry_run_names_its_way_and_changes_nothing(self):
        # Mutation: the download made in a dry run. Red: the app is in place.
        url, sha = self.zipped()
        dry_download = "  (dry) download " + url + "; kept if its sha256, signature and team check, else built here"
        for script, extra, line in ((self.released(url, sha), {}, dry_download),
                                    (self.released("", ""), {}, NOT_PUBLISHED),
                                    (self.released(url, sha), {"PENDIFY_BUILD_APP": "1"}, FORCED)):
            with self.subTest(line=line):
                self.assertEqual(self.app_line(self.install(script, PENDIFY_DRYRUN="1", **extra)), line)
                self.assertFalse(self.app.exists())
                self.assertFalse(self.record.exists())
                self.assertEqual(list(self.temp.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
