"""PackageShapeTest: standard library only, no binary, no dependency, no script table, no launcher."""
import ast
import os
import re
import shutil
import subprocess
import sys
import tomllib
import unittest
import zipfile

import support

BINARY_SUFFIXES = (".exe", ".dll", ".pyd")
LICENSE_HEAD = ["MIT License", "", "Copyright (c) 2026 ElkinDev", "",
                "Permission is hereby granted, free of charge, to any person obtaining a copy"]
README_SECTIONS = ((r"^## Espa\S*ol\s*$(.*?)(?=^## )", "Licencia: MIT"),
                   (r"^## English\s*$(.*?)(?=^## |\Z)", "License: MIT"))
# The program's name before the rename of 2026-10-01, spelled on this one line only, for the tree pin to search for.
OLD_NAME = "pcnotify"
NEW_NAME = "pendify"
REPOSITORY = "https://github.com/ElkinDev/PendiFy"
WORKFLOWS = support.ROOT / ".github" / "workflows"
# The pending publisher on PyPI names this file and this environment; a rename breaks the trust.
PUBLISH = WORKFLOWS / "publish.yml"
RELEASE_ONLY = "if: github.event_name == 'release'"


def top_level_block(lines, key):
    """The lines under the top-level `key:` of a workflow, up to the next top-level key."""
    start = lines.index(key + ":")
    block = []
    for line in lines[start + 1:]:
        if line and not line[0].isspace() and not line.startswith("#"):
            break
        block.append(line)
    return block


def nested_block(lines, header, indent):
    """The lines under `header` (spelled with its indent) that are indented deeper than `indent` spaces."""
    start = lines.index(header)
    block = []
    for line in lines[start + 1:]:
        if line.strip() and not line.strip().startswith("#") and len(line) - len(line.lstrip()) <= indent:
            break
        block.append(line)
    return block


def job_steps(lines, job):
    """The steps of `job` as text blocks, in order, each starting at its `- ` line."""
    steps = []
    for line in nested_block(nested_block(lines, "  " + job + ":", 2), "    steps:", 4):
        if line.startswith("      - "):
            steps.append(line)
        elif steps:
            steps[-1] += "\n" + line
    return steps


def package_files():
    return sorted(support.package_dir().glob("*.py"))


def imports_outside(paths, allowed):
    """(file, module) for every absolute import of `paths` that is neither the standard library nor `allowed`."""
    outside = []
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module]
            else:
                continue
            outside += [(path.name, name) for name in names
                        if name.split(".")[0] not in sys.stdlib_module_names | allowed]
    return outside


class PackageShapeTest(unittest.TestCase):
    def test_every_import_is_the_package_itself_or_the_standard_library(self):
        # Mutation: `import requests` in one module. Red: requests is not in sys.stdlib_module_names.
        self.assertGreaterEqual(len(package_files()), 8)
        self.assertEqual(imports_outside(package_files(), {support.PACKAGE}), [])

    def test_the_icon_tools_import_only_the_standard_library_and_their_grid(self):
        # Mutation: `from PIL import Image` in tools/make_icon.py. Red: PIL is not in sys.stdlib_module_names.
        tools = sorted((support.ROOT / "tools").glob("*.py"))
        self.assertEqual([path.name for path in tools], ["icon_grid.py", "make_icon.py"])
        self.assertEqual(imports_outside(tools, {"icon_grid"}), [])

    def test_the_package_lists_its_icon_and_the_pyproject_ships_it(self):
        # Mutation: the package-data table dropped. Red: setuptools would leave pendify.ico out of the wheel.
        self.assertTrue((support.package_dir() / "pendify.ico").is_file(), "no pendify.ico beside the modules")
        # Mutation: tray.py renamed or moved out of the package. Red: no icon by the clock beside the modules.
        self.assertIn("tray.py", [path.name for path in package_files()])
        # Mutation: update.py left out of the package. Red: no update beside the modules (lane pfupd).
        self.assertIn("update.py", [path.name for path in package_files()])
        pyproject = tomllib.loads((support.ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(pyproject["tool"]["setuptools"]["package-data"], {support.PACKAGE: ["*.ico"]})

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

    def test_the_license_is_mit_in_its_file_the_pyproject_and_both_readme_sections(self):
        # Mutation: LICENSE removed. Red: no license file at the root.
        path = support.ROOT / "LICENSE"
        self.assertTrue(path.is_file(), "no LICENSE at the root")
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual((lines[:5], lines[-1]), (LICENSE_HEAD, "SOFTWARE."))
        pyproject = tomllib.loads((support.ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        # license as an SPDX string with license-files is the form setuptools 77 and newer takes without a warning
        self.assertEqual((pyproject["project"]["license"], pyproject["project"]["license-files"],
                          pyproject["build-system"]["requires"]), ("MIT", ["LICENSE"], ["setuptools>=77"]))
        readme = (support.ROOT / "README.md").read_text(encoding="utf-8")
        for pattern, last in README_SECTIONS:
            section = re.search(pattern, readme, re.M | re.S)
            self.assertIsNotNone(section, pattern)
            self.assertEqual(section.group(1).strip().splitlines()[-1], last)

    def test_the_only_workflow_runs_on_a_published_release_and_by_hand_and_never_on_push(self):
        # Mutation: a `push:` trigger beside the release. Red: the trigger list is not release and
        # workflow_dispatch alone. Every commit subject here ends in [skip ci], which skips push runs.
        self.assertTrue(WORKFLOWS.is_dir(), "no .github/workflows")
        self.assertEqual(sorted(path.name for path in WORKFLOWS.iterdir()), ["publish.yml"])
        lines = PUBLISH.read_text(encoding="utf-8").splitlines()
        triggers = top_level_block(lines, "on")
        self.assertEqual([line.strip() for line in triggers if re.match(r"^  \S", line)],
                         ["release:", "workflow_dispatch:"])
        self.assertIn("    types: [published]", triggers)
        self.assertEqual([line for line in lines if re.match(r"^\s*push\s*:", line) or "[push" in line], [])
        self.assertEqual([line.strip() for line in top_level_block(lines, "permissions") if line.strip()],
                         ["contents: read"])

    def test_the_publish_job_runs_only_on_the_release_in_the_pypi_environment_with_an_id_token(self):
        # Mutation: `contents: write` added to the publish job. Red: its permissions are not id-token alone.
        lines = PUBLISH.read_text(encoding="utf-8").splitlines()
        publish = nested_block(lines, "  publish:", 2)
        for line in ("    needs: build", "    " + RELEASE_ONLY, "    environment: pypi"):
            self.assertIn(line, publish)
        self.assertEqual([line.strip() for line in nested_block(publish, "    permissions:", 4) if line.strip()],
                         ["id-token: write"])
        upload = [step for step in job_steps(lines, "publish") if "pypa/gh-action-pypi-publish@release/v1" in step]
        self.assertEqual(len(upload), 1)
        self.assertNotIn("with:", upload[0])

    def test_the_workflow_names_no_secret_and_checks_the_tag_before_the_build(self):
        # Mutation: `password: ${{ secrets.PYPI_API_TOKEN }}` under the publish step. Red: three words found.
        lines = PUBLISH.read_text(encoding="utf-8").splitlines()
        for word in ("password", "secrets.", "pypi_api_token"):
            self.assertEqual([line for line in lines if word in line.lower()], [], word)
        # Mutation: the tag check step dropped. Red: no step reads the release tag.
        steps = job_steps(lines, "build")
        check = [index for index, step in enumerate(steps) if "github.event.release.tag_name" in step]
        build = [index for index, step in enumerate(steps) if "run: python -m build" in step]
        self.assertEqual(len(check), 1)
        self.assertEqual(len(build), 1)
        self.assertLess(check[0], build[0])
        for part in (RELEASE_ONLY, "pyproject.toml", '"v$version"', "exit 1"):
            self.assertIn(part, steps[check[0]])

    def test_the_sdist_prunes_the_tests_folder(self):
        # Mutation: MANIFEST.in removed. Red: setuptools adds tests/test_*.py to the sdist on its own, without
        # their helpers, so a public download carries tests that cannot run.
        manifest = support.ROOT / "MANIFEST.in"
        self.assertTrue(manifest.is_file(), "no MANIFEST.in at the root")
        lines = [line.strip() for line in manifest.read_text(encoding="utf-8").splitlines()]
        self.assertIn("prune tests", lines)

    def test_pyproject_points_its_urls_at_the_repository_and_keeps_its_version(self):
        # Mutation: Source pointed at a fork. Red: the urls are not the repository's.
        project = tomllib.loads((support.ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual((project["name"], project["version"]), (support.PACKAGE, "0.1.7"))
        self.assertEqual(project.get("urls"), {"Homepage": REPOSITORY, "Source": REPOSITORY})
        self.assertIn("Operating System :: Microsoft :: Windows", project.get("classifiers", []))
        # The license is the SPDX expression; a License classifier beside it is refused by setuptools.
        self.assertEqual([item for item in project.get("classifiers", []) if item.startswith("License")], [])

    # The working-name pin was retired on 2026-09-30: the public name is spelled by the installer, the
    # uninstaller and the README (the repository slug stays one constant in install.ps1).

    def test_the_former_name_is_gone_from_every_tracked_path_and_line_but_this_search(self):
        # Mutation: worker.py names itself by the former name again. Red: the count gains src/pendify/worker.py.
        # Mutation: the icon file kept under its former name. Red: ls-files lists that path.
        git = shutil.which("git")
        self.assertIsNotNone(git, "git is needed to read the tracked tree")

        def tracked(*args):
            done = subprocess.run([git, "-C", str(support.ROOT), *args], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=60)
            return done.returncode, done.stdout.splitlines(), done.stderr

        code, paths, err = tracked("ls-files")
        self.assertEqual(code, 0, err)
        self.assertIn("pyproject.toml", paths)
        self.assertEqual([path for path in paths if OLD_NAME in path.lower()], [])
        # Counted per file, so one new stray line anywhere reds it; this file holds the one line that spells it.
        code, lines, err = tracked("grep", "-c", "-i", "-e", OLD_NAME)
        self.assertEqual(code, 0, err)
        counts = {path: int(count) for path, count in (line.rsplit(":", 1) for line in lines)}
        self.assertEqual(counts, {"tests/test_package_shape.py": 1})

        # The package, its icon and its command answer under the new name.
        self.assertEqual(support.PACKAGE, NEW_NAME)
        self.assertTrue((support.package_dir() / (NEW_NAME + ".ico")).is_file())
        project = tomllib.loads((support.ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual((project["project"]["name"], project["tool"]["setuptools"]["package-data"]),
                         (NEW_NAME, {NEW_NAME: ["*.ico"]}))
        env = {**os.environ, "PYTHONPATH": str(support.SRC)}
        helped = subprocess.run([sys.executable, "-m", NEW_NAME, "--help"], cwd=str(support.SRC), env=env,
                                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(helped.returncode, 0, helped.stdout + helped.stderr)
        self.assertTrue(helped.stdout.startswith("usage: python -m " + NEW_NAME + " "), helped.stdout)

    def test_the_built_wheel_holds_no_binary_and_no_launcher(self):
        # Mutation: a [project.scripts] entry. Red: the wheel carries an entry_points.txt. Mutation: LICENSE
        # removed. Red: no license to build with.
        self.assertTrue((support.ROOT / "LICENSE").is_file(), "no LICENSE at the root")
        with support.temp_dir() as work:
            source = os.path.join(work, "source")
            os.makedirs(source)
            for name in ("pyproject.toml", "README.md", "LICENSE"):
                shutil.copy(support.ROOT / name, source)
            shutil.copytree(support.SRC, os.path.join(source, "src"), ignore=shutil.ignore_patterns("__pycache__"))
            out = os.path.join(work, "dist")
            env = {**os.environ, "TMP": work, "TEMP": work, "PIP_NO_INDEX": "1", "PIP_NO_INPUT": "1",
                   "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
            build = subprocess.run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "--no-build-isolation",
                                    "--no-index", "--disable-pip-version-check", "-v", "-w", out],
                                   cwd=source, env=env, capture_output=True, text=True, timeout=300)
            if build.returncode != 0:
                last = (build.stderr.strip().splitlines() or ["no output"])[-1]
                self.skipTest(f"pip wheel cannot build with what is installed: {last}")
            wheels = [name for name in os.listdir(out) if name.endswith(".whl")]
            self.assertEqual(len(wheels), 1)
            with zipfile.ZipFile(os.path.join(out, wheels[0])) as wheel:
                names = wheel.namelist()
                info = next(name.rsplit("/", 1)[0] for name in names if name.endswith(".dist-info/METADATA"))
                metadata = wheel.read(info + "/METADATA").decode("utf-8").splitlines()
                carried = wheel.read(info + "/licenses/LICENSE") if info + "/licenses/LICENSE" in names else None
        # The license form is one this setuptools takes without a deprecation warning.
        self.assertNotIn("SetuptoolsDeprecationWarning", build.stdout + build.stderr)
        self.assertEqual([line for line in metadata if line.startswith(("License-Expression:", "License-File:"))],
                         ["License-Expression: MIT", "License-File: LICENSE"])
        self.assertEqual(carried, (support.ROOT / "LICENSE").read_bytes())
        self.assertEqual([name for name in names if name.lower().endswith(BINARY_SUFFIXES)], [])
        self.assertEqual([name for name in names if name.endswith("entry_points.txt")], [])
        # Mutation: the package-data table dropped. Red: the wheel carries the modules and no pendify.ico.
        self.assertEqual({name.split("/")[-1] for name in names if name.startswith(support.PACKAGE + "/")},
                         {path.name for path in package_files()} | {"pendify.ico"})
        # Mutation: tray.py left out of the package. Red: the icon by the clock is not in the wheel.
        self.assertIn(support.PACKAGE + "/tray.py", names)
        self.assertIn(support.PACKAGE + "/update.py", names)


if __name__ == "__main__":
    unittest.main()
