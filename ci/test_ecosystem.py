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
