import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import build_image
import render_buildspec

REQUEST = "00000000-0000-4000-8000-000000000001"
BUILD = "00000000-0000-4000-8000-000000000002"


class BuildImageTest(unittest.TestCase):
    def setUp(self):
        self.env = dict(S3_KEY=f"prob-7/builds/{REQUEST}/source.zip", CODEBUILD_BUILD_ID=f"syslab-practice-build:{BUILD}",
                        ACCOUNT_ID="123456789012", AWS_REGION="ap-northeast-2", ECR_REPOSITORY="practice", PRACTICE_SOURCE_BUCKET="source-bucket")
        self.calls = []

    def fake_run(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[:3] == ["aws", "s3", "cp"]:
            with zipfile.ZipFile(args[4], "w") as archive:
                archive.writestr("Dockerfile", "FROM scratch\n")
        return SimpleNamespace(stdout="mock-login-token")

    def test_success_downloads_exact_request_and_pushes_unique_build_tag(self):
        image = build_image.build_image(self.env, self.fake_run)
        self.assertTrue(image.endswith(f":build-{BUILD}"))
        self.assertEqual(self.calls[0][0][3], f"s3://source-bucket/prob-7/builds/{REQUEST}/source.zip")
        self.assertEqual(self.calls[-1][0], ["docker", "push", image])
        self.assertEqual(self.calls[2][1]["input"], "mock-login-token")
        self.assertNotIn("mock-login-token", self.calls[2][0])

    def test_build_failure_never_pushes_or_reports_success(self):
        def run(args, **kwargs):
            if args[:2] == ["docker", "build"]:
                raise subprocess.CalledProcessError(1, args)
            return self.fake_run(args, **kwargs)
        with self.assertRaises(subprocess.CalledProcessError):
            build_image.build_image(self.env, run)
        self.assertFalse(any(args[:2] == ["docker", "push"] for args, _ in self.calls))

    def test_push_failure_propagates(self):
        def run(args, **kwargs):
            if args[:2] == ["docker", "push"]:
                raise subprocess.CalledProcessError(1, args)
            return self.fake_run(args, **kwargs)
        with self.assertRaises(subprocess.CalledProcessError):
            build_image.build_image(self.env, run)

    def test_old_or_injected_key_is_rejected_before_external_calls(self):
        for key in ["prob-7/source.zip", "../source.zip", f"prob-7/builds/{REQUEST}/source.zip; echo bad"]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    build_image.build_image({**self.env, "S3_KEY": key}, self.fake_run)
        self.assertEqual(self.calls, [])

    def test_duplicate_requests_get_distinct_tags_for_distinct_builds(self):
        first = build_image.configuration(self.env)[-1]
        second = build_image.configuration({**self.env, "CODEBUILD_BUILD_ID": f"syslab-practice-build:{REQUEST}"})[-1]
        self.assertNotEqual(first, second)

    def test_unsafe_zip_entries_rejected(self):
        for name in ["../outside", "/absolute", "C:/absolute", "dir\\file"]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with zipfile.ZipFile(root / "input.zip", "w") as z:
                    z.writestr(name, "data")
                with self.assertRaises(ValueError):
                    build_image.unpack(root / "input.zip", root / "context")

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with zipfile.ZipFile(root / "input.zip", "w") as z:
                info = zipfile.ZipInfo("link")
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                z.writestr(info, "../outside")
            with self.assertRaises(ValueError):
                build_image.unpack(root / "input.zip", root / "context")

    def test_missing_root_dockerfile_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with zipfile.ZipFile(root / "input.zip", "w") as z:
                z.writestr("nested/Dockerfile", "FROM scratch")
            with self.assertRaises(ValueError):
                build_image.unpack(root / "input.zip", root / "context")

    def test_generated_inline_buildspec_matches_tested_script(self):
        self.assertEqual(Path(__file__).with_name("buildspec.yml").read_text(encoding="utf-8"), render_buildspec.render())

    def test_event_rule_covers_every_build_state_without_post_build_notification(self):
        rule = json.loads(Path(__file__).with_name("result-event-pattern.json").read_text(encoding="utf-8"))
        self.assertEqual(rule["detail-type"], ["CodeBuild Build State Change"])
        self.assertEqual(rule["detail"], {"project-name": ["syslab-practice-build"]})


if __name__ == "__main__":
    unittest.main()
