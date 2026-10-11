import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import ecosystem


class InfrastructureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def record(self, name="example", revision="a" * 40):
        return {name: {"revision": revision, "kind": "library"}}

    def read_inventory(self, records):
        file = self.root / "repositories.json"
        file.write_text(json.dumps(records))
        return ecosystem.inventory(file)

    def manifest(self, name, text, nested=""):
        directory = self.root / name / nested
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "goml.toml").write_text(text)

    def test_inventory_requires_safe_names_and_immutable_revisions(self):
        for name in ["../example", "bad/name", "", "verification"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.read_inventory(self.record(name))
        for revision in ["main", "v0.1.0", "abc123", "A" * 40]:
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                self.read_inventory(self.record(revision=revision))
        self.assertEqual(self.read_inventory(self.record()), self.record())

    def test_inventory_rejects_duplicate_repository_and_record_keys(self):
        file = self.root / "repositories.json"
        record = json.dumps(self.record()["example"])
        for text in [f'{{"example":{record},"example":{record}}}',
                     '{"example":{"revision":"' + "a" * 40 + '","revision":"'
                     + "b" * 40 + '","kind":"library"}}']:
            with self.subTest(text=text):
                file.write_text(text)
                with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
                    ecosystem.inventory(file)

    def test_inventory_reports_invalid_record_types_as_validation_errors(self):
        for record in [None, True, 42, "invalid", [], ["revision", "kind"],
                       {"revision": 42, "kind": "library"},
                       {"revision": "a" * 40, "kind": []}]:
            with self.subTest(record=record):
                with self.assertRaisesRegex(ValueError, "example"):
                    self.read_inventory({"example": record})

    def test_unknown_modules_are_rejected(self):
        for name in [None, "../example", "other"]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                ecosystem.selected_module(name, self.record())

    def test_dependency_closure_includes_dev_and_native_fixture_dependencies(self):
        records = self.record("app") | self.record("core") | self.record("helper")
        self.manifest("app", '[dependencies]\n"ecosystem::core"="0.1.0"\n')
        self.manifest("app", '[dev-dependencies]\n"ecosystem::helper"="0.1.0"\n', "testdata/downstream/native")
        self.manifest("core", '[dependencies]\n"ecosystem::app"="0.1.0"\n')
        self.manifest("helper", '[module]\npath="ecosystem::helper"\n')
        self.manifest("app", '[dependencies]\n"ecosystem::missing"="0.1.0"\n', "_artifact/ignored")
        self.assertEqual(ecosystem.dependency_closure(self.root, "app", records), ["app", "core", "helper"])

    def test_unknown_dependency_is_a_failure(self):
        self.manifest("app", '[dependencies]\n"ecosystem::missing"="0.1.0"\n')
        with self.assertRaisesRegex(ValueError, "unknown ecosystem repository"):
            ecosystem.dependency_closure(self.root, "app", self.record("app"))

    def test_ecosystem_dependencies_cannot_resolve_to_applications_or_catalogs(self):
        for name, kind in [("explorer", "application"), ("ecosystem", "catalog")]:
            with self.subTest(kind=kind):
                self.manifest("app", f'[dependencies]\n"ecosystem::{name}"="0.1.0"\n')
                self.manifest(name, f'[module]\npath="example::{name}"\n')
                records = self.record("app") | {name: {"kind": kind, "revision": "a" * 40}}
                with self.assertRaisesRegex(ValueError, f"ecosystem::{name}.*app/goml.toml"):
                    ecosystem.dependency_closure(self.root, "app", records)

    def test_missing_registered_dependency_is_a_failure_with_source(self):
        self.manifest("app", '[dependencies]\n"ecosystem::core"="0.1.0"\n', "testdata/downstream/native")
        self.manifest("app", '[module]\npath="example::app"\n')
        records = self.record("app") | self.record("core")
        with self.assertRaisesRegex(ValueError, "dependency core.*app/testdata/downstream/native/goml.toml"):
            ecosystem.dependency_closure(self.root, "app", records)
        (self.root / "core").mkdir()
        with self.assertRaisesRegex(ValueError, "dependency core.*goml.toml"):
            ecosystem.dependency_closure(self.root, "app", records)

    def test_missing_or_linked_selected_sources_are_rejected(self):
        records = self.record("app")
        with self.assertRaisesRegex(ValueError, "selected module app"):
            ecosystem.dependency_closure(self.root, "app", records)
        self.manifest("external", '[module]\npath="ecosystem::app"\n')
        (self.root / "app").symlink_to(self.root / "external", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "selected module app"):
            ecosystem.dependency_closure(self.root, "app", records)
        (self.root / "app").unlink()
        (self.root / "app").mkdir()
        (self.root / "app/goml.toml").symlink_to(self.root / "external/goml.toml")
        with self.assertRaisesRegex(ValueError, "selected module app"):
            ecosystem.dependency_closure(self.root, "app", records)

    def test_source_walk_errors_are_not_silently_ignored(self):
        self.manifest("app", '[module]\npath="ecosystem::app"\n')
        failure = PermissionError("unreadable source directory")
        def walk(path, *, onerror=None):
            if onerror is not None:
                onerror(failure)
            return iter(())
        with patch.object(ecosystem.os, "walk", side_effect=walk):
            with self.assertRaisesRegex(PermissionError, "unreadable source directory"):
                ecosystem.dependency_closure(self.root, "app", self.record("app"))

    def test_native_preflight_failure_never_starts_downloads(self):
        self.manifest("app", '[dependencies]\n"ecosystem::core"="0.1.0"\n')
        (self.root / "app/go.mod").write_text("module example.com/app\n")
        with patch.object(ecosystem, "run") as run:
            with self.assertRaises(ValueError):
                ecosystem.native(self.root, "app", self.record("app") | self.record("core"))
        run.assert_not_called()

    def test_catalog_native_discovery_needs_no_goml_manifest(self):
        (self.root / "ecosystem").mkdir()
        records = {"ecosystem": {"kind": "catalog", "revision": "a" * 40}}
        with patch.object(ecosystem, "run") as run:
            ecosystem.native(self.root, "ecosystem", records)
        run.assert_not_called()

    def test_invalid_manifest_sections_fail_with_a_source_path(self):
        for content in ['dependencies=["ecosystem::core"]\n', 'build="bad"\n',
                        '[build]\ntarget-dir="."\n', '[build]\ntarget-dir="build\\\\cache"\n',
                        'invalid TOML']:
            with self.subTest(content=content):
                self.manifest("app", content)
                with self.assertRaisesRegex(ValueError, "app/goml.toml"):
                    ecosystem.dependency_closure(self.root, "app", self.record("app") | self.record("core"))

    def test_prepare_preserves_candidate_revision(self):
        records = self.record("app") | self.record("dependency")
        (self.root / "app/.git").mkdir(parents=True)
        self.manifest("verification", '[module]\npath="ecosystem::verification"\n')
        with patch.object(ecosystem, "checkout") as checkout, patch.object(ecosystem, "run", return_value="b" * 40 + "\n"):
            ecosystem.prepare(self.root, "app", records)
        checkout.assert_called_once_with(self.root, "dependency", "a" * 40)
        report = json.loads((self.root / "verification/_artifact/ci/checkouts.json").read_text())
        self.assertEqual(report["revisions"]["app"], "b" * 40)

    def test_existing_dependency_with_wrong_revision_is_not_overwritten(self):
        (self.root / "dependency").mkdir()
        with patch.object(ecosystem, "run", return_value="b" * 40 + "\n") as run:
            with self.assertRaisesRegex(ValueError, "unexpected revision"):
                ecosystem.checkout(self.root, "dependency", "a" * 40)
        self.assertEqual(run.call_count, 1)

    def git_fixture(self, name):
        directory = self.root / name
        directory.mkdir()
        def git(*arguments):
            return subprocess.run(["git", *arguments], cwd=directory, check=True,
                                  text=True, capture_output=True).stdout.strip()
        git("init", "--quiet")
        (directory / "source.goml").write_text("package original;\n")
        (directory / ".gitignore").write_text("_artifact/\n")
        git("add", "source.goml", ".gitignore")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            "commit", "--quiet", "-m", "fixture")
        return directory, git("rev-parse", "HEAD"), git

    def test_reused_dependency_requires_clean_tracked_and_untracked_sources(self):
        for change in ["modified", "staged", "untracked"]:
            with self.subTest(change=change):
                directory, revision, git = self.git_fixture(change)
                source = directory / ("extra.goml" if change == "untracked" else "source.goml")
                source.write_text("package changed;\n")
                if change == "staged":
                    git("add", "source.goml")
                with self.assertRaisesRegex(ValueError, "dependency checkout has local changes"):
                    ecosystem.checkout(self.root, change, revision)
                self.assertEqual(source.read_text(), "package changed;\n")
                self.assertEqual(git("rev-parse", "HEAD"), revision)

    def test_clean_dependency_reuse_preserves_ignored_build_artifacts(self):
        directory, revision, git = self.git_fixture("dependency")
        output = directory / "_artifact/generated.goml"
        output.parent.mkdir()
        output.write_text("generated output")
        ecosystem.checkout(self.root, "dependency", revision)
        self.assertEqual(output.read_text(), "generated output")
        self.assertEqual(git("rev-parse", "HEAD"), revision)

    def test_dependency_directory_cannot_borrow_a_parent_git_repository(self):
        directory, revision, _ = self.git_fixture("parent")
        (directory / "dependency").mkdir()
        with self.assertRaisesRegex(ValueError, "not a repository root"):
            ecosystem.checkout(directory, "dependency", revision)

    def test_dependency_checkout_does_not_follow_directory_symlinks(self):
        directory, revision, _ = self.git_fixture("original")
        (self.root / "dependency").symlink_to(directory, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            ecosystem.checkout(self.root, "dependency", revision)

    def test_native_downloads_only_dependency_closure(self):
        self.manifest("app", '[dependencies]\n"ecosystem::core"="0.1.0"\n')
        self.manifest("core", '[module]\npath="ecosystem::core"\n')
        self.manifest("unused", '[module]\npath="ecosystem::unused"\n')
        for name in ["app", "core", "unused"]:
            (self.root / name / "go.mod").write_text("module example.com/" + name + "\n")
        with patch.object(ecosystem, "run") as run:
            ecosystem.native(self.root, "app", self.record("app") | self.record("core") | self.record("unused"))
        self.assertEqual([call.kwargs["cwd"].name for call in run.call_args_list], ["app", "core"])

    def test_native_downloads_nested_source_modules_and_skips_generated_files(self):
        records = self.record("app") | self.record("core")
        self.manifest("app", '[build]\ntarget-dir="build/cache"\n[dependencies]\n"ecosystem::core"="0.1.0"\n')
        self.manifest("core", '[module]\npath="ecosystem::core"\n')
        paths = [
            "app/examples/basic", "app/testdata/downstream/codec", "core",
            "app/_artifact/native", "app/_bootstrap/native", "app/node_modules/native",
            "app/.goml/cache/registry/native", "app/.cache/native", "app/vendor/native",
            "app/build/cache/native",
        ]
        for relative in paths:
            directory = self.root / relative
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "go.mod").write_text("module example.com/native\n")
        linked = self.root / "app/linked"
        linked.mkdir()
        (linked / "go.mod").symlink_to(self.root / "core/go.mod")
        (self.root / "app/linked-directory").symlink_to(self.root / "core", target_is_directory=True)
        with patch.object(ecosystem, "run") as run:
            ecosystem.native(self.root, "app", records)
        self.assertEqual(
            [call.kwargs["cwd"].relative_to(self.root).as_posix() for call in run.call_args_list],
            ["app/examples/basic", "app/testdata/downstream/codec", "core"],
        )
        for call in run.call_args_list:
            self.assertEqual(call.args[0], ["go", "mod", "download", "all"])

    def test_native_dependency_download_failures_are_not_ignored(self):
        self.manifest("app", '[module]\npath="ecosystem::app"\n')
        (self.root / "app/go.mod").write_text("module example.com/app\n")
        failure = subprocess.CalledProcessError(1, ["go", "mod", "download", "all"])
        with patch.object(ecosystem, "run", side_effect=failure), self.assertRaises(subprocess.CalledProcessError):
            ecosystem.native(self.root, "app", self.record("app"))

    def test_catalog_rejects_runner_inventory_drift(self):
        with self.assertRaisesRegex(ValueError, "runner/catalog mismatch"):
            ecosystem.validate_catalog(self.root, self.record("example"), {"other"})

    def test_postgres_verification_requires_live_service_before_running_commands(self):
        for dsn in [None, "", " \t\n"]:
            environment = {} if dsn is None else {"GOML_POSTGRES_TEST_DSN": dsn}
            with self.subTest(dsn=dsn), patch.dict(ecosystem.os.environ, environment, clear=True):
                with patch.object(ecosystem, "run") as run:
                    with self.assertRaisesRegex(ValueError, "GOML_POSTGRES_TEST_DSN"):
                        ecosystem.verify(self.root, "postgres", Path("/goml"), self.record("postgres"))
                run.assert_not_called()

    def test_postgres_live_service_is_passed_to_all_verification_commands(self):
        dsn = "postgresql://postgres:postgres@127.0.0.1:15432/goml_test?sslmode=disable"
        with patch.dict(ecosystem.os.environ, {"GOML_POSTGRES_TEST_DSN": dsn}, clear=True):
            with patch.object(ecosystem, "run") as run:
                ecosystem.verify(self.root, "postgres", Path("/goml"), self.record("postgres"))
        self.assertEqual(run.call_count, 3)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["env"]["GOML_POSTGRES_TEST_DSN"], dsn)
        self.assertEqual(run.call_args.args[0][-1], "postgres")

    def test_source_revision_is_validated_before_checkout(self):
        config = self.root / "toolchain.json"
        config.write_text(json.dumps({"source_revision": "main"}))
        with patch.object(ecosystem, "run") as run:
            with self.assertRaisesRegex(ValueError, "full commit SHA"):
                ecosystem.install(self.root / "prefix", config)
        run.assert_not_called()

    def test_source_install_finalizes_built_binaries_and_records_revision(self):
        config = self.root / "toolchain.json"
        revision = "b" * 40
        config.write_text(json.dumps({"source_revision": revision}))
        prefix = self.root / "prefix"
        def checkout(root, name, selected):
            self.assertEqual((name, selected), ("goml", revision))
            for project, names in (("gomlc", ("gomlc", "gomlfmt", "gomldoc", "gomllsp")),
                                   ("goml", ("goml",))):
                for name in names:
                    binary = root / "goml" / project / f"_bootstrap/stage2/bin/cmd/{name}/{name}"
                    binary.parent.mkdir(parents=True, exist_ok=True)
                    binary.write_text("built " + name)
        def run(arguments, **kwargs):
            if "tools/goml-go-meta/build.sh" in arguments:
                (prefix / "bin").mkdir(parents=True)
            if "tools/lib/finalize-toolchain.sh" in arguments:
                self.assertEqual((prefix / "bin/goml").read_text(), "built goml")
                self.assertEqual((prefix / "bin/gomlc").read_text(), "built gomlc")
        with patch.object(ecosystem, "checkout", side_effect=checkout), \
                patch.object(ecosystem, "install_release") as released, \
                patch.object(ecosystem, "run", side_effect=run):
            ecosystem.install(prefix, config)
        self.assertEqual(released.call_args.args[0].name, "stage0")
        self.assertEqual((prefix / "source-revision").read_text(), revision + "\n")

    def test_checksum_failure_prevents_toolchain_execution(self):
        config = self.root / "toolchain.json"
        config.write_text(json.dumps({"version": "0.1.57", "sha256": "a" * 64}))
        def download(arguments, **kwargs):
            Path(arguments[-2]).write_bytes(b"invalid archive")
        with patch.object(ecosystem, "run", side_effect=download) as run:
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                ecosystem.install(self.root / "prefix", config)
        self.assertEqual(run.call_count, 1)
        self.assertFalse((self.root / "prefix").exists())


if __name__ == "__main__":
    unittest.main()
