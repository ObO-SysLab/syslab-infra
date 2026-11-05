import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile
from package_release import package, REQUIRED


class ReleasePackageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        for name in REQUIRED:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name, encoding="utf-8")

    def test_bundle_has_both_lambda_modules_and_verified_checksums(self):
        destination = Path(self.temp.name) / "release"
        manifest = package(self.root, destination, "a" * 40)
        with zipfile.ZipFile(destination / "grading-lambda.zip") as archive:
            self.assertEqual(set(archive.namelist()), {"index.mjs", "launch.mjs"})
            self.assertEqual(archive.read("index.mjs"), (self.root / "grader/lambda_trigger.mjs").read_bytes())
        self.assertEqual(manifest["lambdaHandlers"]["grading-lambda.zip"], "index.handler")
        self.assertFalse(manifest["deploymentApproved"])
        for name, digest in manifest["archives"].items():
            self.assertEqual(digest, hashlib.sha256((destination / name).read_bytes()).hexdigest())
        other = Path(self.temp.name) / "repeat"
        self.assertEqual(manifest, package(self.root, other, "a" * 40))

    def test_missing_dependency_fails_before_creating_release(self):
        (self.root / "grader/launch.mjs").unlink()
        destination = Path(self.temp.name) / "release"
        with self.assertRaises(ValueError):
            package(self.root, destination, "a" * 40)
        self.assertFalse(destination.exists())

    def test_excludes_test_assets_but_keeps_runtime_contract_and_build_dockerfile(self):
        excluded = ("grader/tests/test_coordinator.py", "grader/runner/test_linux_sandbox.py",
                    "grader/runner/Dockerfile.obo-test", "grader/runner/diagram-fixtures.json",
                    "vm/ws-server/auth.test.js", "vm/lifecycle/test_lifecycle.py",
                    "vm/codebuild/test_build_image.py", "vm/codebuild/sqs_message_sample.json")
        included = ("grader/obo_runtime/diagram-contract.json", "vm/codebuild/Dockerfile")
        for name in excluded + included:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name, encoding="utf-8")
        destination = Path(self.temp.name) / "release"
        manifest = package(self.root, destination, "a" * 40)
        with zipfile.ZipFile(destination / "infrastructure.zip") as archive:
            for name in excluded:
                self.assertNotIn(name, archive.namelist())
                self.assertNotIn(name, manifest["files"])
            for name in included:
                self.assertIn(name, archive.namelist())

    def test_refuses_mutable_revision_or_overwrite(self):
        destination = Path(self.temp.name) / "release"
        with self.assertRaises(ValueError):
            package(self.root, destination, "main")
        package(self.root, destination, "a" * 40)
        with self.assertRaises(FileExistsError):
            package(self.root, destination, "b" * 40)


if __name__ == "__main__":
    unittest.main()
