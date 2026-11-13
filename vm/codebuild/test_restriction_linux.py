"""Real Linux probes of the practice command restriction layer.

Needs Docker and network access for base images, so it runs only with DIVEON_RESTRICTION_LINUX=1
(the CI docker step). Ordinary unit runs skip it. Images are local and removed afterwards.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

import build_image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lifecycle"))
from diveon_vm_helper import RESTRICTED_OPTIONS  # noqa: E402  (the VM helper's real create options)

ENABLED = os.environ.get("DIVEON_RESTRICTION_LINUX") == "1" and shutil.which("docker") is not None
DEBIAN_LIKE = """FROM {base}
RUN apt-get update && apt-get install -y --no-install-recommends vim-tiny && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /home/lab && echo 'DK{{fake_flag}}' > /home/lab/flag.txt && chmod 600 /home/lab/flag.txt
"""
ALLOWED = ["cat", "find", "ls", "vi"]


def docker(*args, check=True):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, timeout=900)


@unittest.skipUnless(ENABLED, "set DIVEON_RESTRICTION_LINUX=1 on a Linux host with Docker")
class RestrictionLinuxTest(unittest.TestCase):
    created = []

    @classmethod
    def build(cls, source_dockerfile, names):
        tag = "diveon-restriction-test:" + uuid.uuid4().hex[:12]
        source = tag + "-src"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "src").mkdir()
            (root / "src" / "Dockerfile").write_text(source_dockerfile, encoding="utf-8")
            docker("build", "-t", source, str(root / "src"))
            cls.created.append(source)
            build_image.restriction_layer(root / "layer", source, names)
            # Same flags as build_image.build_image, plus plain progress so RUN output is visible.
            result = docker("build", "--progress=plain", "--network", "none", "-t", tag, str(root / "layer"),
                            check=False)
        if result.returncode == 0:
            cls.created.append(tag)
        return tag, result

    @classmethod
    def setUpClass(cls):
        cls.images = {}
        for base in ["ubuntu:22.04", "debian:12"]:
            tag, result = cls.build(DEBIAN_LIKE.format(base=base), ALLOWED)
            if result.returncode != 0:
                raise AssertionError(f"{base} restriction build failed:\n{result.stdout}\n{result.stderr}")
            cls.images[base] = tag

    @classmethod
    def tearDownClass(cls):
        for image in cls.created:
            docker("image", "rm", "-f", image, check=False)

    def shell(self, image, script):
        result = docker("run", "--rm", "--network", "none", image, "/bin/bash", "-c", script, check=False)
        return result.stdout + result.stderr

    def each(self):
        for base, image in self.images.items():
            with self.subTest(base=base):
                yield image

    def test_shell_runs_as_lab_in_its_home(self):
        for image in self.each():
            self.assertEqual(self.shell(image, 'echo "$EUID $PWD $HOME"').strip(), "10001 /home/lab /home/lab")

    def test_interactive_terminal_starts_without_permission_errors(self):
        # The terminal opens an interactive bash, which reads /etc/bash.bashrc.
        for image in self.each():
            output = self.shell(image, "exec /bin/bash -i -c 'echo TERMINAL_READY'")
            self.assertIn("TERMINAL_READY", output)
            self.assertNotIn("Permission denied", output)

    def test_allowed_commands_work_and_home_files_are_readable_not_writable(self):
        for image in self.each():
            output = self.shell(image, "ls / > /dev/null && cat /home/lab/flag.txt && find /home/lab -name flag.txt"
                                       " && vi --version > /dev/null && echo ALLOWED_OK")
            self.assertIn("DK{fake_flag}", output)
            self.assertIn("/home/lab/flag.txt", output)
            self.assertIn("ALLOWED_OK", output)
            output = self.shell(image, "echo x >> /home/lab/flag.txt; echo rc=$?; echo x > /home/lab/new; echo rc=$?;"
                                       " echo x > /home/lab/work/new && cat /home/lab/work/new && echo WORK_OK")
            self.assertEqual(output.count("rc=1"), 2)
            self.assertIn("WORK_OK", output)

    def test_other_commands_are_denied_by_name_and_by_path(self):
        for image in self.each():
            for command in ["id", "/usr/bin/id", "perl -e 1", "/bin/sh -c true", "su -c true", "apt-get --version",
                            "chmod +x /home/lab/flag.txt"]:
                with self.subTest(command=command):
                    self.assertIn("rc=126", self.shell(image, f"{command}; echo rc=$?"))

    def test_shell_escapes_through_allowed_tools_do_not_run_other_programs(self):
        for image in self.each():
            probes = {
                "find -exec sh": "find /home/lab -maxdepth 0 -exec /bin/sh -c 'echo ESCAPED' \\;",
                "find -exec bash": "find /home/lab -maxdepth 0 -exec /bin/bash -c id \\;",
                "vi shell": "vi -es -c 'set shell=/bin/bash' -c '!id' -c 'q!' < /dev/null",
                "vi sh": "vi -es -c '!id' -c 'q!' < /dev/null",
                "dynamic loader": "for l in /lib64/ld-linux-*.so.* /lib/ld-linux-*.so.* /lib/*/ld-linux-*.so.*; do"
                                  " [ -e \"$l\" ] && \"$l\" /usr/bin/id; done; true",
            }
            for name, script in probes.items():
                with self.subTest(probe=name):
                    output = self.shell(image, script)
                    self.assertNotIn("ESCAPED", output)
                    self.assertNotIn("uid=", output)

    def test_denied_programs_cannot_be_read_either(self):
        for image in self.each():
            self.assertIn("rc=1", self.shell(image, "cat /usr/bin/id > /dev/null; echo rc=$?"))

    def test_vm_helper_and_terminal_path_use_the_image_user(self):
        # Mirrors diveon-vm-helper (create with --init, sleep infinity) and ws-server (plain docker exec).
        for image in self.each():
            container = docker("run", "-d", "--network", "none", "--init", "--memory", "256m", *RESTRICTED_OPTIONS,
                               image, "sleep", "infinity").stdout.strip()
            try:
                self.assertEqual(docker("container", "inspect", "--format", "{{.State.Status}}", container)
                                 .stdout.strip(), "running")
                self.assertEqual(docker("exec", container, "/bin/bash", "-c", 'echo "$EUID $PWD"').stdout.strip(),
                                 "10001 /home/lab")
            finally:
                docker("rm", "-f", container, check=False)

    def test_script_interpreters_are_kept_for_allowed_scripts(self):
        # debianutils "which" is a /bin/sh script: allowing it must keep sh, and nothing else.
        tag, result = self.build("FROM ubuntu:22.04\n", ["which"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("/which", self.shell(tag, "which which"))
        self.assertIn("rc=126", self.shell(tag, "id; echo rc=$?"))

    def test_unrestrictable_images_fail_the_build(self):
        cases = {
            "missing command": ("FROM ubuntu:22.04\n", ["definitely-missing-cmd"], "command not found in image",
                                ("COMMAND_NOT_FOUND", "definitely-missing-cmd")),
            "existing lab account": ("FROM ubuntu:22.04\nRUN useradd lab\n", ["ls"], "already exists",
                                     ("LAB_ACCOUNT_EXISTS", None)),
            "busybox": ("FROM alpine:3.20\nRUN apk add --no-cache bash\n", ["ls"], "busybox", ("BUSYBOX_COMMAND", "sleep")),
        }
        for name, (dockerfile, names, reason, failure) in cases.items():
            with self.subTest(case=name):
                _, result = self.build(dockerfile, names)
                self.assertNotEqual(result.returncode, 0)
                output = result.stdout + result.stderr
                self.assertIn(reason, output)
                # The same parser the build uses for the author-facing result.json.
                self.assertEqual(build_image.restriction_failure(output), failure)

    def test_programs_written_by_lab_cannot_run_under_the_vm_helper_mounts(self):
        # A planted executable (made by root here, since lab cannot chmod) runs without the helper's
        # noexec mounts and is refused with them: the mounts are what block it.
        plant = "printf '#!/bin/bash\\necho PLANTED_RAN\\n' > {0}/x && chmod 755 {0}/x"
        for image in self.each():
            for options, expected in [([], "PLANTED_RAN"), (RESTRICTED_OPTIONS, "rc=126")]:
                container = docker("run", "-d", "--network", "none", "--init", *options, image,
                                   "sleep", "infinity").stdout.strip()
                try:
                    for place in ["/home/lab/work", "/tmp"]:
                        with self.subTest(hardened=bool(options), place=place):
                            docker("exec", "-u", "0", container, "/bin/bash", "-c", plant.format(place))
                            result = docker("exec", container, "/bin/bash", "-c", f"{place}/x; echo rc=$?", check=False)
                            self.assertIn(expected, result.stdout + result.stderr)
                finally:
                    docker("rm", "-f", container, check=False)

    def test_loader_cannot_map_programs_from_noexec_mounts(self):
        for image in self.each():
            container = docker("run", "-d", "--network", "none", "--init", *RESTRICTED_OPTIONS, image,
                               "sleep", "infinity").stdout.strip()
            try:
                docker("exec", "-u", "0", container, "/bin/bash", "-c",
                       "cp /usr/bin/id /home/lab/work/id && chmod 755 /home/lab/work/id")
                probe = ('for l in /lib64/ld-linux-*.so.* /lib/ld-linux-*.so.*; do [ -e "$l" ] && "$l" /home/lab/work/id;'
                         ' done; echo PROBED')
                output = docker("exec", container, "/bin/bash", "-c", probe, check=False)
                self.assertIn("PROBED", output.stdout)
                self.assertNotIn("uid=", output.stdout + output.stderr)
            finally:
                docker("rm", "-f", container, check=False)


if __name__ == "__main__":
    unittest.main()
