import unittest

from workflows import workflow


class WorkflowTests(unittest.TestCase):
    def test_external_workflow_and_checkout_use_the_same_immutable_revision(self):
        revision = "a" * 40
        result = workflow("sql", revision)
        self.assertIn("ecosystem-ci.yml@" + revision, result)
        self.assertIn("verification-ref: " + revision, result)
        self.assertIn("module: sql", result)
        self.assertNotIn("secrets:", result)
        self.assertNotIn("pull_request_target", result)

    def test_verification_tests_its_candidate_infrastructure(self):
        result = workflow("verification", "a" * 40)
        self.assertIn("uses: ./.github/workflows/ecosystem-ci.yml", result)
        self.assertIn("verification-ref: ${{ github.sha }}", result)

    def test_mutable_refs_and_injected_module_names_are_rejected(self):
        for module, revision in [("../sql", "a" * 40), ("sql\n", "a" * 40), ("sql", "main")]:
            with self.subTest(module=module, revision=revision), self.assertRaises(ValueError):
                workflow(module, revision)


if __name__ == "__main__":
    unittest.main()
