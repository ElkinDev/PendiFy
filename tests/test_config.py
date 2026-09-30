"""ConfigStoreTest: the one JSON file holding exactly secret and linkId (design P1, P6, residual e)."""
import contextlib
import io
import json
import os
import threading
import unittest
import urllib.request
from pathlib import Path

import support
from support import LINK_ID, SECRET

try:
    import msvcrt
except ImportError:  # not Windows
    msvcrt = None

codes = support.module("codes")
config = support.module("config")
entry = support.module("__main__")

WINDOWS_ONLY = ("another handle that holds config.json against a read (msvcrt.locking) or a replace (an open "
                "handle without delete sharing) is a Windows behavior")


class ConfigStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = support.temp_dir()
        self.base = Path(self._tmp.name)
        self.store = config.ConfigStore(self.base)
        self.folder = self.base / config.FOLDER_NAME
        # A lockfile never written: a run of the entry point watches no client, never the real one.
        self.no_client = str(self.base / "no client" / "lockfile")

    def tearDown(self):
        self._tmp.cleanup()

    def on_disk(self):
        return json.loads(self.store.path.read_text(encoding="utf-8"))

    def write_raw(self, text):
        self.folder.mkdir(parents=True, exist_ok=True)
        self.store.path.write_text(text, encoding="utf-8")

    def test_the_file_is_config_json_in_a_folder_named_after_the_package(self):
        # Mutation: the file written straight under the base. Red: the path lacks the folder.
        self.assertEqual(config.FOLDER_NAME, support.PACKAGE)
        self.assertEqual(self.store.path, self.base / support.PACKAGE / "config.json")

    def test_a_first_load_mints_and_writes_and_a_second_load_reads_the_same_pair(self):
        # Mutation: load mints without writing. Red: the file is missing and the second load mints anew.
        first = self.store.load()
        self.assertEqual(codes.normalize(first.secret), first.secret)
        self.assertIsNone(first.link_id)
        self.assertEqual(self.on_disk(), {"secret": first.secret, "linkId": None})
        second = config.ConfigStore(self.base).load()
        self.assertEqual((second.secret, second.link_id), (first.secret, None))

    def test_a_stored_pair_is_read_back_normalized(self):
        # Mutation: read keeps the raw text. Red: the dashed lower-case secret comes back as written.
        self.write_raw(json.dumps({"secret": "abcd-2345-efgh", "linkId": "wxyz-6789-abcd"}))
        loaded = self.store.load()
        self.assertEqual((loaded.secret, loaded.link_id), (SECRET, LINK_ID))

    def test_missing_empty_corrupt_or_unnormalizable_files_are_a_first_load(self):
        # Mutation: a corrupt file is kept and raises. Red: json.JSONDecodeError out of load.
        cases = {
            "empty": "",
            "corrupt": "{not json",
            "a list": "[]",
            "no secret": json.dumps({"linkId": LINK_ID}),
            "eleven symbols": json.dumps({"secret": "ABCD2345EFG", "linkId": LINK_ID}),
            "a number": json.dumps({"secret": 12, "linkId": LINK_ID}),
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                self.write_raw(text)
                loaded = self.store.load()
                self.assertEqual(codes.normalize(loaded.secret), loaded.secret)
                self.assertIsNone(loaded.link_id)
                self.assertEqual(self.on_disk(), {"secret": loaded.secret, "linkId": None})
        self.store.path.write_bytes(b"\xff\xfe{")
        self.assertIsNone(self.store.load().link_id)
        self.store.path.unlink()
        self.assertEqual(self.store.read(), None)
        self.assertEqual(self.on_disk() if self.store.path.exists() else "absent", "absent")

    def test_a_malformed_link_id_beside_a_good_secret_reads_as_no_link_id(self):
        # Mutation: the link id is returned raw. Red: WXYZ6789ABC comes back.
        self.write_raw(json.dumps({"secret": SECRET, "linkId": "WXYZ6789ABC"}))
        loaded = self.store.load()
        self.assertEqual((loaded.secret, loaded.link_id), (SECRET, None))

    def test_every_write_holds_two_keys_and_leaves_one_file(self):
        # Mutation: the temp file is renamed with a copy instead of os.replace. Red: a .tmp remains beside it.
        self.write_raw(json.dumps({"secret": SECRET, "linkId": None, "extra": "kept?"}))
        self.store.load()
        self.store.set_typed(LINK_ID, SECRET)
        self.assertEqual(self.on_disk(), {"secret": SECRET, "linkId": LINK_ID})
        self.store.forget()
        self.assertEqual(set(self.on_disk()), {"secret", "linkId"})
        self.assertEqual(sorted(os.listdir(self.folder)), ["config.json"])

    def test_a_failed_write_raises_and_leaves_no_temp_file(self):
        # Mutation: the temp file is not removed when os.replace fails. Red: a second entry in the folder.
        self.store.path.mkdir(parents=True)
        with self.assertRaises(OSError):
            self.store.set_typed(LINK_ID, SECRET)
        self.assertEqual(sorted(os.listdir(self.folder)), ["config.json"])
        self.assertTrue(self.store.path.is_dir())

    def test_forget_mints_a_new_secret_and_clears_the_link_id(self):
        # Mutation: forget keeps the secret and only clears the link id. Red: the secret is unchanged.
        self.store.set_typed(LINK_ID, SECRET)
        forgotten = self.store.forget()
        self.assertNotEqual(forgotten.secret, SECRET)
        self.assertEqual(codes.normalize(forgotten.secret), forgotten.secret)
        self.assertIsNone(forgotten.link_id)
        self.assertEqual(self.on_disk(), {"secret": forgotten.secret, "linkId": None})

    def test_the_typed_pair_is_stored_normalized_and_a_malformed_one_is_refused(self):
        # Mutation: set_typed stores the raw text. Red: the dashed form lands in the file.
        self.store.load()
        stored = self.store.set_typed("wxyz-6789-abcd", " abcd 2345 efgh ")
        self.assertEqual((stored.secret, stored.link_id), (SECRET, LINK_ID))
        self.assertEqual(self.on_disk(), {"secret": SECRET, "linkId": LINK_ID})
        for link_id, secret in (("WXYZ6789ABC", SECRET), (LINK_ID, "ABCD2345EFG0"), ("", ""), (None, SECRET)):
            with self.subTest(link_id=link_id, secret=secret):
                with self.assertRaises(ValueError) as caught:
                    self.store.set_typed(link_id, secret)
                self.assertNotIn("WXYZ", str(caught.exception))
                self.assertNotIn("ABCD", str(caught.exception))
                self.assertEqual(self.on_disk(), {"secret": SECRET, "linkId": LINK_ID})

    def test_the_link_id_is_set_and_cleared_keeping_the_secret(self):
        # Mutation: clear_link_id mints a new secret. Red: the secret differs after the clear.
        secret = self.store.load().secret
        self.assertEqual(self.store.set_link_id("wxyz-6789-abcd").link_id, LINK_ID)
        cleared = self.store.clear_link_id()
        self.assertEqual((cleared.secret, cleared.link_id), (secret, None))
        self.assertEqual(self.on_disk(), {"secret": secret, "linkId": None})
        with self.assertRaises(ValueError):
            self.store.set_link_id("WXYZ6789ABC")

    def test_a_pairing_never_shows_its_values_in_its_repr(self):
        # Mutation: the dataclass fields keep repr=True. Red: the secret is in the repr.
        text = repr(self.store.set_typed(LINK_ID, SECRET))
        self.assertNotIn(SECRET, text)
        self.assertNotIn(LINK_ID, text)

    def test_the_base_directory_is_appdata_and_its_absence_is_loud(self):
        # Mutation: a missing APPDATA falls back to the working directory. Red: no RuntimeError.
        self.assertEqual(config.default_base_dir({"APPDATA": "C:/Users/a b/AppData/Roaming"}),
                         Path("C:/Users/a b/AppData/Roaming"))
        with self.assertRaises(RuntimeError):
            config.default_base_dir({})
        with self.assertRaises(RuntimeError):
            config.default_base_dir({"APPDATA": "  "})

    def test_a_base_with_spaces_and_forward_slashes_works(self):
        # Mutation: the base is split on spaces. Red: the file lands in the wrong folder.
        spaced = (self.base / "a folder with spaces").as_posix()
        store = config.ConfigStore(spaced)
        secret = store.load().secret
        self.assertEqual(json.loads((self.base / "a folder with spaces" / config.FOLDER_NAME / "config.json")
                                    .read_text(encoding="utf-8"))["secret"], secret)

    @contextlib.contextmanager
    def held(self, lock):
        """config.json open in another handle, as another program holds it; with lock, its bytes locked."""
        handle = open(self.store.path, "rb")
        size = self.store.path.stat().st_size
        try:
            if lock:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, size)
            try:
                yield handle
            finally:
                if lock:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, size)
        finally:
            handle.close()

    def unavailable(self):
        return config.UNAVAILABLE.format(path=self.store.path)

    @unittest.skipIf(msvcrt is None, WINDOWS_ONLY)
    def test_a_file_another_handle_locks_raises_the_one_sentence_and_keeps_the_pair(self):
        # Mutation: read catches only FileNotFoundError. Red: a raw PermissionError out of load.
        self.store.set_typed(LINK_ID, SECRET)
        with self.held(lock=True):
            with self.assertRaises(OSError) as caught:
                self.store.load()
        self.assertIs(type(caught.exception), config.ConfigError)
        self.assertEqual(str(caught.exception), self.unavailable())
        self.assertIsNone(caught.exception.__cause__)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertEqual(self.on_disk(), {"secret": SECRET, "linkId": LINK_ID})

    @unittest.skipIf(msvcrt is None, WINDOWS_ONLY)
    def test_the_page_driver_and_the_ping_command_print_the_one_sentence_and_exit_1(self):
        # Mutation: main lets ConfigError out. Red: an exception in place of exit 1 and the sentence.
        self.store.set_typed(LINK_ID, SECRET)
        stop, opened = threading.Event(), []
        stop.set()
        for argv, kwargs in ((["ping", "lol_queue_found"], {}), ([], {"opener": opened.append, "stop": stop})):
            with self.subTest(argv=argv):
                out, err = io.StringIO(), io.StringIO()
                with self.held(lock=True), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        code = entry.main(["--data-dir", str(self.base), "--worker", "http://127.0.0.1:9",
                                           "--client-lockfile", self.no_client, *argv],
                                          **kwargs, beep=support.silent_beep)
                    except Exception as failure:  # the red: what leaves main in place of the exit code
                        code = type(failure).__name__
                self.assertEqual((code, out.getvalue(), err.getvalue()), (1, self.unavailable() + "\n", ""))
        self.assertEqual(opened, [])
        self.assertEqual(self.on_disk(), {"secret": SECRET, "linkId": LINK_ID})

    @unittest.skipIf(msvcrt is None, WINDOWS_ONLY)
    def test_a_replace_refused_twice_is_retried_and_one_never_allowed_ends_in_the_sentence(self):
        # Mutation: the retry removed from the replace. Red: the first PermissionError ends the write.
        self.store.load()
        pauses, outcome = [], None
        with self.held(lock=False) as handle:
            def pause(seconds):
                pauses.append(seconds)
                if len(pauses) == 2:
                    handle.close()  # the other program lets go after the second refusal
            try:
                config.ConfigStore(self.base, pause=pause).set_typed(LINK_ID, SECRET)
            except OSError as failure:
                outcome = type(failure).__name__
        self.assertEqual((outcome, pauses), (None, [0.2, 0.2]))
        self.assertEqual(self.on_disk(), {"secret": SECRET, "linkId": LINK_ID})
        self.assertEqual(sorted(os.listdir(self.folder)), ["config.json"])
        pauses.clear()
        with self.held(lock=False):
            with self.assertRaises(OSError) as caught:
                config.ConfigStore(self.base, pause=pauses.append).forget()
        self.assertIs(type(caught.exception), config.ConfigError)
        self.assertEqual(str(caught.exception), self.unavailable())
        self.assertEqual(pauses, [0.2] * 5)  # five retries over one second
        self.assertEqual(self.on_disk(), {"secret": SECRET, "linkId": LINK_ID})
        self.assertEqual(sorted(os.listdir(self.folder)), ["config.json"])

    def test_a_config_file_deleted_mid_run_raises_the_one_sentence_and_the_page_driver_exits_1(self):
        # Mutation: _current raises RuntimeError again. Red: RuntimeError in place of ConfigError and of exit 1.
        self.store.load()
        self.store.path.unlink()
        outcomes = []
        for store_it in (lambda: self.store.set_link_id(LINK_ID), self.store.clear_link_id):
            try:
                outcomes.append(store_it() and None)
            except Exception as failure:  # the red: whatever leaves the store in place of ConfigError
                outcomes.append((type(failure).__name__, str(failure)))
        self.assertEqual(outcomes, [("ConfigError", self.unavailable())] * 2)
        fake = support.FakeWorker(lambda path, body: (200, {"linkId": LINK_ID}))
        self.addCleanup(fake.close)
        stop, opened = threading.Event(), []

        def opener(url):  # the browser's first fetch opens the check window, then the file goes
            opened.append(url)
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url + "state", timeout=5):
                pass
            self.store.path.unlink()
            stop.set()  # one tick at most: a driver that swallows the failure ends, never hangs
            return True

        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = entry.main(["--data-dir", str(self.base), "--worker", fake.base, "--client-lockfile",
                                   self.no_client], opener=opener, stop=stop, beep=support.silent_beep)
            except Exception as failure:  # the red: what leaves main in place of the exit code
                code = type(failure).__name__
        self.assertEqual((code, out.getvalue(), err.getvalue()),
                         (1, f"page: {opened[0]}\n{self.unavailable()}\n", ""))
        self.assertEqual(len(fake.bodies()), 1)
        self.assertFalse(self.store.path.exists())


if __name__ == "__main__":
    unittest.main()
