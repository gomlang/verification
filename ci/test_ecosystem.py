import json
from pathlib import Path
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
