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
        self.metadata = {}
        self.layers = []
        self.reports = []
        self.zip_entries = {"Dockerfile": "FROM scratch\n"}

    def fake_run(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[:3] == ["aws", "s3api", "get-object"]:
            with zipfile.ZipFile(args[-1], "w") as archive:
                for name, data in self.zip_entries.items():
                    archive.writestr(name, data)
            return SimpleNamespace(stdout=json.dumps({"ContentLength": 1, "Metadata": self.metadata}))
        if args[:3] == ["aws", "s3api", "put-object"]:
            body = Path(args[args.index("--body") + 1]).read_text(encoding="utf-8")
            self.reports.append((args[args.index("--key") + 1], json.loads(body)))
        if args[:2] == ["docker", "build"]:
            context = Path(args[-1])
            if (context / "diveon-restrict.sh").exists():
                self.layers.append((context / "Dockerfile").read_text(encoding="utf-8"))
        return SimpleNamespace(stdout="mock-login-token")

    def builds(self):
        return [args for args, _ in self.calls if args[:2] == ["docker", "build"]]

    def pushes(self):
        return [args[2] for args, _ in self.calls if args[:2] == ["docker", "push"]]

    def test_success_downloads_exact_request_and_pushes_unique_build_tag(self):
        image = build_image.build_image(self.env, self.fake_run)
        self.assertTrue(image.endswith(f":build-{BUILD}"))
        download = self.calls[0][0]
        self.assertEqual(download[download.index("--bucket") + 1], "source-bucket")
        self.assertEqual(download[download.index("--key") + 1], f"prob-7/builds/{REQUEST}/source.zip")
        self.assertEqual(self.calls[-1][0], ["docker", "push", image])
        self.assertEqual(self.calls[2][1]["input"], "mock-login-token")
        self.assertNotIn("mock-login-token", self.calls[2][0])

    def test_without_policy_metadata_the_build_is_unchanged(self):
        image = build_image.build_image(self.env, self.fake_run)
        [only] = self.builds()
        self.assertEqual(only[:4], ["docker", "build", "-t", image])
        self.assertEqual(len(only), 5)
        self.assertEqual(self.pushes(), [image])
        self.assertEqual(self.layers, [])

    def test_policy_adds_trusted_layer_and_pushes_marker_tag_on_same_build(self):
        self.metadata = {"diveon-commands": "v1:cat,ls", "other": "ignored"}
        image = build_image.build_image(self.env, self.fake_run)
        source, layer = self.builds()
        self.assertEqual(source[:4], ["docker", "build", "-t", f"diveon-src:build-{BUILD}"])
        self.assertEqual(layer[:4], ["docker", "build", "--network", "none"])
        self.assertEqual(layer[4:8], ["-t", image, "-t", f"{image}-cmd-eeba41b2b2019ca3"])
        self.assertEqual(self.pushes(), [image, f"{image}-cmd-eeba41b2b2019ca3"])
        dockerfile = self.layers[0]
        self.assertTrue(dockerfile.startswith(f"FROM diveon-src:build-{BUILD}\nUSER root\n"))
        self.assertIn('RUN ["/bin/bash", "/diveon-restrict.sh", "cat", "ls"]\n', dockerfile)
        self.assertTrue(dockerfile.endswith(
            "USER 10001:10001\nWORKDIR /home/lab\nENV HOME=/home/lab HISTFILE=/tmp/.bash_history\nENTRYPOINT []\n"))

    def test_marker_matches_backend_canonical_vectors(self):
        self.assertEqual(build_image.policy_marker(["cat", "ls"]), "cmd-eeba41b2b2019ca3")
        self.assertEqual(build_image.policy_marker(["find", "strings", "xxd"]), "cmd-7a12e1cb6c42c378")

    def test_invalid_policy_metadata_fails_before_any_docker_call(self):
        for value in ["", "v1:", "v2:ls", "ls", "v1:ls,cat", "v1:cat,cat", "v1:-rf", "v1:a b", "v1:$(id)",
                      "v1:" + "a" * 33, "v1:" + ",".join(f"c{i:02d}" for i in range(31)), 7]:
            with self.subTest(value=value):
                self.calls.clear()
                self.metadata = {"diveon-commands": value}
                with self.assertRaises(ValueError):
                    build_image.build_image(self.env, self.fake_run)
                self.assertFalse(any(args[0] == "docker" for args, _ in self.calls))

    def test_policy_accepts_limits(self):
        names = sorted(f"c{i:02d}" for i in range(30))
        self.assertEqual(build_image.command_policy({"diveon-commands": "v1:" + ",".join(names)}), names)
        self.assertEqual(build_image.command_policy({"diveon-commands": "v1:" + "a" * 32}), ["a" * 32])
        self.assertIsNone(build_image.command_policy({}))
        with self.assertRaises(ValueError):
            build_image.command_policy(None)

    def test_restricted_layer_build_failure_never_pushes(self):
        self.metadata = {"diveon-commands": "v1:ls"}
        def run(args, **kwargs):
            if args[:4] == ["docker", "build", "--network", "none"]:
                raise subprocess.CalledProcessError(64, args)
            return self.fake_run(args, **kwargs)
        with self.assertRaises(subprocess.CalledProcessError):
            build_image.build_image(self.env, run)
        self.assertEqual(self.pushes(), [])

    def failing(self, prefix, error):
        def run(args, **kwargs):
            if args[:len(prefix)] == prefix:
                self.calls.append((args, kwargs))
                raise error
            return self.fake_run(args, **kwargs)
        return run

    def assert_report(self, code, command=None):
        result_key = f"prob-7/builds/{REQUEST}/result.json"
        self.assertEqual(self.reports, [(result_key, {"version": 1, "code": code, **({"command": command} if command else {})})])

    def test_author_failures_are_reported_next_to_the_source(self):
        cases = [
            ("DOCKERFILE_FAILED", {}, ["docker", "build", "-t"], subprocess.CalledProcessError(1, "build")),
            ("PUSH_FAILED", {}, ["docker", "push"], subprocess.CalledProcessError(1, "push")),
            ("DOCKERFILE_FAILED", {"diveon-commands": "v1:ls"}, ["docker", "build", "-t", f"diveon-src:build-{BUILD}"],
             subprocess.CalledProcessError(1, "build")),
            ("RESTRICTION_FAILED", {"diveon-commands": "v1:ls"}, ["docker", "build", "--network"],
             subprocess.CalledProcessError(64, "build")),
        ]
        for code, metadata, prefix, error in cases:
            with self.subTest(code=code, restricted=bool(metadata)):
                self.calls, self.reports, self.metadata = [], [], metadata
                with self.assertRaises(subprocess.CalledProcessError):
                    build_image.build_image(self.env, self.failing(prefix, error))
                self.assert_report(code)

    def test_restriction_reason_and_command_come_from_the_layer_output(self):
        self.metadata = {"diveon-commands": "v1:ls,xxd"}
        output = "#7 0.2 DIVEON_RESTRICTION_ERROR code=COMMAND_NOT_FOUND arg=xxd command not found in image: xxd\n"
        error = subprocess.CalledProcessError(64, "build", output="", stderr=output)
        with self.assertRaises(subprocess.CalledProcessError):
            build_image.build_image(self.env, self.failing(["docker", "build", "--network"], error))
        self.assert_report("COMMAND_NOT_FOUND", "xxd")

    def test_unknown_reason_or_unsafe_argument_is_never_passed_on(self):
        self.assertEqual(build_image.restriction_failure("DIVEON_RESTRICTION_ERROR code=SOMETHING arg=ls"), ("RESTRICTION_FAILED", None))
        self.assertEqual(build_image.restriction_failure("DIVEON_RESTRICTION_ERROR code=BUSYBOX_COMMAND arg=$(id)"), ("BUSYBOX_COMMAND", None))
        self.assertEqual(build_image.restriction_failure("DIVEON_RESTRICTION_ERROR code=LAB_ACCOUNT_EXISTS arg=- x"), ("LAB_ACCOUNT_EXISTS", None))
        self.assertEqual(build_image.restriction_failure(None), ("RESTRICTION_FAILED", None))

    def test_source_and_policy_problems_are_reported_without_docker(self):
        for code, entries, metadata in [("SOURCE_INVALID", {"nested/Dockerfile": "FROM scratch"}, {}),
                                        ("POLICY_INVALID", {"Dockerfile": "FROM scratch"}, {"diveon-commands": "v1:-rf"})]:
            with self.subTest(code=code):
                self.calls, self.reports, self.zip_entries, self.metadata = [], [], entries, metadata
                with self.assertRaises(ValueError):
                    build_image.build_image(self.env, self.fake_run)
                self.assert_report(code)
                self.assertFalse(any(args[0] == "docker" for args, _ in self.calls))

    def test_platform_faults_and_success_write_no_report(self):
        build_image.build_image(self.env, self.fake_run)
        with self.assertRaises(subprocess.CalledProcessError):
            build_image.build_image(self.env, self.failing(["aws", "ecr"], subprocess.CalledProcessError(1, "login")))
        with self.assertRaises(subprocess.CalledProcessError):
            build_image.build_image(self.env, self.failing(["aws", "s3api", "get-object"], subprocess.CalledProcessError(1, "get")))
        self.assertEqual(self.reports, [])

    def test_report_upload_failure_keeps_the_original_error(self):
        def run(args, **kwargs):
            if args[:3] == ["aws", "s3api", "put-object"]:
                raise subprocess.CalledProcessError(1, "put")
            if args[:3] == ["docker", "build", "-t"]:
                raise subprocess.CalledProcessError(1, "build")
            return self.fake_run(args, **kwargs)
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            build_image.build_image(self.env, run)
        self.assertEqual(raised.exception.cmd, "build")

    def test_restricted_layer_labels_image_for_the_vm_helper(self):
        self.metadata = {"diveon-commands": "v1:ls"}
        build_image.build_image(self.env, self.fake_run)
        self.assertIn("LABEL net.diveon.restriction=v1\n", self.layers[0])
        self.assertIn("ENV HOME=/home/lab HISTFILE=/tmp/.bash_history\n", self.layers[0])

    def test_restriction_script_keeps_inline_buildspec_heredoc_intact(self):
        self.assertNotIn("DIVEON_BUILD_PY", build_image.RESTRICT_SCRIPT)
        self.assertNotIn("\t", build_image.RESTRICT_SCRIPT)
        self.assertTrue(build_image.RESTRICT_SCRIPT.startswith("#!/bin/bash\nset -euo pipefail\n"))

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
