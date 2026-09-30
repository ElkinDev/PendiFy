"""PackageShapeTest: standard library only, no binary, no dependency, no script table, no launcher."""
import ast
import os
import shutil
import subprocess
import sys
import tomllib
import unittest
import zipfile

import support

BINARY_SUFFIXES = (".exe", ".dll", ".pyd")


def package_files():
    return sorted(support.package_dir().glob("*.py"))


class PackageShapeTest(unittest.TestCase):
    def test_every_import_is_the_package_itself_or_the_standard_library(self):
        # Mutation: `import requests` in one module. Red: requests is not in sys.stdlib_module_names.
        self.assertGreaterEqual(len(package_files()), 8)
        outside = []
        for path in package_files():
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module]
                else:
                    continue
                outside += [(path.name, name) for name in names
                            if name.split(".")[0] not in sys.stdlib_module_names | {support.PACKAGE}]
        self.assertEqual(outside, [])

    def test_winsound_is_imported_only_behind_its_guard(self):
        # Mutation: `import winsound` at the top of a module, outside its try. Red: one unguarded import.
        guarded, every = set(), []
        for path in package_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) and "winsound" in [alias.name for alias in node.names]:
                    every.append((path.name, node.lineno))
                if isinstance(node, ast.Try) and any(
                        isinstance(handler.type, ast.Name) and handler.type.id in ("ImportError", "ModuleNotFoundError")
                        for handler in node.handlers):
                    guarded |= {(path.name, inner.lineno) for statement in node.body for inner in ast.walk(statement)
                                if isinstance(inner, ast.Import)}
        self.assertEqual(len(every), 1)
        self.assertEqual([place for place in every if place not in guarded], [])

    def test_the_tree_holds_no_exe_dll_or_pyd(self):
        # Mutation: a launcher.exe beside the package. Red: the walk finds it.
        found = []
        for folder, subfolders, files in os.walk(support.ROOT):
            subfolders[:] = [name for name in subfolders if name != ".git"]
            found += [os.path.join(folder, name) for name in files if name.lower().endswith(BINARY_SUFFIXES)]
        self.assertEqual(found, [])

    def test_pyproject_declares_no_dependency_and_no_script_table(self):
        # Mutation: a [project.scripts] entry. Red: the table is present.
        project = tomllib.loads((support.ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(project["name"], support.PACKAGE)
        self.assertEqual(project["requires-python"], ">=3.10")
        self.assertEqual(project.get("dependencies", []), [])
        for table in ("optional-dependencies", "scripts", "gui-scripts", "entry-points"):
            self.assertNotIn(table, project)

    # The working-name pin was retired on 2026-09-30: pcnotify is the public name now, spelled by the
    # installer, the uninstaller and the README (the repository slug stays one constant in install.ps1).

    def test_the_built_wheel_holds_no_binary_and_no_launcher(self):
        # Mutation: a [project.scripts] entry. Red: the wheel carries an entry_points.txt.
        with support.temp_dir() as work:
            source = os.path.join(work, "source")
            os.makedirs(source)
            for name in ("pyproject.toml", "README.md"):
                shutil.copy(support.ROOT / name, source)
            shutil.copytree(support.SRC, os.path.join(source, "src"), ignore=shutil.ignore_patterns("__pycache__"))
            out = os.path.join(work, "dist")
            env = {**os.environ, "TMP": work, "TEMP": work, "PIP_NO_INDEX": "1", "PIP_NO_INPUT": "1",
                   "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
            build = subprocess.run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--no-build-isolation",
                                    "--no-index", "--disable-pip-version-check", "-w", out],
                                   cwd=source, env=env, capture_output=True, text=True, timeout=300)
            if build.returncode != 0:
                last = (build.stderr.strip().splitlines() or ["no output"])[-1]
                self.skipTest(f"pip wheel cannot build with what is installed: {last}")
            wheels = [name for name in os.listdir(out) if name.endswith(".whl")]
            self.assertEqual(len(wheels), 1)
            with zipfile.ZipFile(os.path.join(out, wheels[0])) as wheel:
                names = wheel.namelist()
        self.assertEqual([name for name in names if name.lower().endswith(BINARY_SUFFIXES)], [])
        self.assertEqual([name for name in names if name.endswith("entry_points.txt")], [])
        self.assertEqual({name.split("/")[-1] for name in names if name.startswith(support.PACKAGE + "/")},
                         {path.name for path in package_files()})


if __name__ == "__main__":
    unittest.main()
