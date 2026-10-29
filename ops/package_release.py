"""Build a checksummed source release and complete Lambda ZIP; never contact AWS."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

REQUIRED = (
    "grader/lambda_trigger.mjs", "grader/launch.mjs", "grader/grader.py",
    "grader/runner_client.py", "grader/execution_protocol.py", "grader/Dockerfile",
    "grader/runner/Dockerfile", "grader/runner/launcher.c", "grader/runner/sandbox.py",
    "grader/runner/worker.py", "grader/runner/task-definition.example.json",
    "vm/ws-server/server.js", "vm/ws-server/auth.js", "vm/ws-server/terminal.js",
    "vm/ws-server/package-lock.json", "vm/lifecycle/diveon_vm_helper.py",
    "vm/codebuild/build_image.py", "vm/codebuild/buildspec.yml",
)
SUFFIXES = {".py", ".mjs", ".js", ".json", ".md", ".txt", ".c", ".yml", ".service", ".sh"}


def archive(destination, files):
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as output:
        for name, payload in sorted(files.items()):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            output.writestr(entry, payload)


def package(root, destination, revision):
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("A full source commit is required")
    root = Path(root).resolve()
    for name in REQUIRED:
        if not (root / name).is_file():
            raise ValueError("Missing release component: " + name)
    files = {}
    for folder in ("grader", "vm"):
        for path in sorted((root / folder).rglob("*")):
            relative = path.relative_to(root)
            if any(p in {"node_modules", "__pycache__", "tests"} for p in relative.parts):
                continue
            if not path.is_file() or (path.suffix not in SUFFIXES and path.name != "Dockerfile"):
                continue
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                raise ValueError("Release file escapes source root")
            files[relative.as_posix()] = path.read_bytes()
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    archive(destination / "infrastructure.zip", files)
    archive(destination / "grading-lambda.zip", {name: files["grader/" + name] for name in ("lambda_trigger.mjs", "launch.mjs")})
    manifest = {
        "sourceRevision": revision,
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
        "archives": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(destination.glob("*.zip"))},
        "deploymentApproved": False,
        "requiredImages": ["coordinator@sha256:<reviewed>", "runner@sha256:<reviewed>"],
        "backendSchema": "Use matching backend release ops SQL; never infer deployment readiness from this archive.",
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    arguments = parser.parse_args()
    package(arguments.root, arguments.output, arguments.revision)
