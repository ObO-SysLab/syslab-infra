"""Trusted CodeBuild orchestration, embedded in the project buildspec (not the uploaded ZIP)."""
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
SOURCE = re.compile(r"prob-([1-9][0-9]*)/builds/(" + UUID + r")/source\.zip")
BUILD = re.compile(r"[A-Za-z0-9_-]+:(" + UUID + r")")

# The backend stores the author's allowed commands as S3 metadata on the exact source object.
POLICY_METADATA = "diveon-commands"
POLICY_PREFIX = "v1:"
COMMAND = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,31}")
MAX_COMMANDS = 30
LAB_ID = 10001
RESTRICTION_LABEL = "net.diveon.restriction"
RESTRICTION_VERSION = "v1"

# Author-facing failure reasons, written to prob-N/builds/<request>/result.json next to the source.
# The S3 trigger only reacts to keys ending in source.zip, so this object never starts a build.
FAILURE_CODES = {"SOURCE_INVALID", "POLICY_INVALID", "DOCKERFILE_FAILED", "COMMAND_NOT_FOUND", "BUSYBOX_COMMAND",
                 "LAB_ACCOUNT_EXISTS", "IMAGE_TOOL_MISSING", "RESTRICTION_FAILED", "PUSH_FAILED"}
RESTRICTION_ERROR = re.compile(r"DIVEON_RESTRICTION_ERROR code=([A-Z_]+) arg=(\S+)")

# Runs as root in the last layer, after the author's Dockerfile. Every executable outside the
# kept set loses group/other read and execute, so the lab account can neither run it nor load it
# through the dynamic loader, whichever shell or tool tries. Arguments are validated names.
# Failures print "DIVEON_RESTRICTION_ERROR code=<CODE> arg=<name|->" for the author-facing reason.
RESTRICT_SCRIPT = r'''#!/bin/bash
set -euo pipefail
set -f
export LC_ALL=C
: "${PATH:=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}"
LAB_ID=10001

fail() { local code=$1 arg=$2; shift 2; printf 'DIVEON_RESTRICTION_ERROR code=%s arg=%s %s\n' "$code" "$arg" "$*" >&2; exit 64; }

[ "$EUID" -eq 0 ] || fail RESTRICTION_FAILED - "the restriction layer must run as root"
[ "$#" -ge 1 ] || fail POLICY_INVALID - "no allowed commands"
for tool in chmod chown find mkdir readlink rm tail; do
  type -P "$tool" >/dev/null || fail IMAGE_TOOL_MISSING "$tool" "image lacks required tool: $tool"
done
{ [ -f /etc/passwd ] && [ -f /etc/group ]; } || fail RESTRICTION_FAILED - "image lacks /etc/passwd or /etc/group"
for table in /etc/passwd /etc/group; do
  while IFS=: read -r name _ id _ || [ -n "${name:-}" ]; do
    { [ "$name" != lab ] && [ "$id" != "$LAB_ID" ]; } || fail LAB_ACCOUNT_EXISTS - "lab or id $LAB_ID already exists in $table"
  done < "$table"
done

declare -A keep=()
keep_file() {
  local real
  real=$(readlink -f -- "$1") || fail COMMAND_NOT_FOUND "$2" "cannot resolve $1"
  [ -f "$real" ] || fail COMMAND_NOT_FOUND "$2" "not a regular file: $1"
  case "${real##*/}" in busybox*) fail BUSYBOX_COMMAND "$2" "busybox multi-call command cannot be restricted: $1" ;; esac
  keep["$real"]=1
  REPLY=$real
}
keep_command() {
  local path
  path=$(type -P -- "$1") || fail COMMAND_NOT_FOUND "$1" "command not found in image: $1"
  keep_file "$path" "$1"
  keep_interpreter "$REPLY" "$2"
}
# An allowed script also needs its "#!" interpreter (and the program named by /usr/bin/env).
keep_interpreter() {
  local file=$1 depth=$2 magic="" line="" interp="" rest="" word
  [ "$depth" -lt 3 ] || return 0
  IFS= read -r -N 2 magic < "$file" 2>/dev/null || true
  [ "$magic" = '#!' ] || return 0
  IFS= read -r line < "$file" || true
  read -r interp rest <<< "${line#??}" || true
  [ -n "$interp" ] || fail RESTRICTION_FAILED - "empty interpreter in $file"
  keep_file "$interp" "${interp##*/}"
  if [ "${interp##*/}" = env ]; then
    for word in $rest; do
      case "$word" in -*|*=*) continue ;; esac
      keep_command "$word" $((depth + 1))
      return 0
    done
  else
    keep_interpreter "$REPLY" $((depth + 1))
  fi
}

keep_command bash 0
keep_command sleep 0
for name in "$@"; do keep_command "$name" 0; done

ensure_newline() { [ ! -s "$1" ] || [ -z "$(tail -c 1 -- "$1")" ] || printf '\n' >> "$1"; }
ensure_newline /etc/passwd
printf 'lab:x:%s:%s:DiveOn lab:/home/lab:/bin/bash\n' "$LAB_ID" "$LAB_ID" >> /etc/passwd
ensure_newline /etc/group
printf 'lab:x:%s:\n' "$LAB_ID" >> /etc/group
if [ -f /etc/shadow ]; then
  ensure_newline /etc/shadow
  printf 'lab:!:19000:0:99999:7:::\n' >> /etc/shadow
fi

# Shared libraries keep their mode: programs need to read them, and they are not commands.
find / -xdev -type f -perm /0111 ! -name '*.so' ! -name '*.so.*' -exec chmod go-rwx -- {} +
for file in "${!keep[@]}"; do chmod o+rx -- "$file"; done
# lab may write only to ~/work and /tmp, which the VM helper mounts noexec.
find / -xdev -type d -perm -0002 ! -path /tmp ! -path /var/tmp -exec chmod o-w -- {} +
find / -xdev -type f -perm -0002 -exec chmod o-w -- {} +

mkdir -p /home/lab
# Ubuntu's /etc/bash.bashrc runs "groups" for its sudo hint on every terminal unless this exists.
[ -e /home/lab/.hushlogin ] || : > /home/lab/.hushlogin
# Author files stay root-owned: lab reads (and runs) them but cannot rewrite them into new programs.
chown -hR 0:0 /home/lab
find /home/lab -xdev -type d -exec chmod o+rx,o-w -- {} +
find /home/lab -xdev -type f -exec chmod o+r,o-w -- {} +
find /home/lab -xdev -type f -perm -0100 -exec chmod o+x -- {} +
mkdir -p /home/lab/work
chown "$LAB_ID:$LAB_ID" /home/lab/work
chmod 0700 /home/lab/work
rm -f -- "$0"
'''


def configuration(env):
    key = env["S3_KEY"]
    build = BUILD.fullmatch(env["CODEBUILD_BUILD_ID"])
    if not SOURCE.fullmatch(key) or not build:
        raise ValueError("Invalid build identity")
    account, region, repo, bucket = (env[name] for name in
        ("ACCOUNT_ID", "AWS_REGION", "ECR_REPOSITORY", "PRACTICE_SOURCE_BUCKET"))
    if not re.fullmatch(r"[0-9]{12}", account) or not re.fullmatch(r"[a-z]{2}-[a-z]+-[0-9]+", region):
        raise ValueError("Invalid registry configuration")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*", repo) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket):
        raise ValueError("Invalid repository or bucket")
    registry = f"{account}.dkr.ecr.{region}.amazonaws.com"
    return key, bucket, region, registry, f"{registry}/{repo}:build-{build.group(1)}"


def command_policy(metadata):
    """Allowed commands from the source object's metadata, or None for an unrestricted build."""
    if not isinstance(metadata, dict):
        raise ValueError("Invalid source object metadata")
    raw = metadata.get(POLICY_METADATA)
    if raw is None:
        return None
    names = raw[len(POLICY_PREFIX):].split(",") if isinstance(raw, str) and raw.startswith(POLICY_PREFIX) else []
    # The backend sends the canonical form: sorted, unique, validated names.
    if (not 1 <= len(names) <= MAX_COMMANDS or any(not COMMAND.fullmatch(name) for name in names)
            or names != sorted(set(names))):
        raise ValueError("Invalid command policy")
    return names


def policy_marker(names):
    """Tag suffix the backend recomputes to confirm this exact policy was applied to the image."""
    canonical = "v1\n" + "\n".join(names)
    return "cmd-" + hashlib.sha256(canonical.encode("ascii")).hexdigest()[:16]


def restriction_layer(directory, source, names):
    """Write the trusted build context that restricts the author's image to the allowed commands."""
    directory.mkdir()
    (directory / "diveon-restrict.sh").write_text(RESTRICT_SCRIPT, encoding="utf-8", newline="\n")
    command = json.dumps(["/bin/bash", "/diveon-restrict.sh", *names])
    # The label tells the VM helper to mount ~/work and /tmp noexec for this image.
    (directory / "Dockerfile").write_text(
        f"FROM {source}\nUSER root\nCOPY diveon-restrict.sh /diveon-restrict.sh\nRUN {command}\n"
        f"LABEL {RESTRICTION_LABEL}={RESTRICTION_VERSION}\n"
        f"USER {LAB_ID}:{LAB_ID}\nWORKDIR /home/lab\nENV HOME=/home/lab HISTFILE=/tmp/.bash_history\nENTRYPOINT []\n",
        encoding="utf-8", newline="\n")


def unpack(archive, destination):
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > 2048 or sum(m.file_size for m in members) > 512 * 1024 * 1024:
            raise ValueError("Build context too large")
        seen = set()
        for member in members:
            path = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if (not member.filename or "\\" in member.filename or ":" in member.filename
                    or path.is_absolute() or ".." in path.parts
                    or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))
                    or str(path) in seen):
                raise ValueError("Unsafe ZIP entry")
            seen.add(str(path))
            target = destination.joinpath(*path.parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(member) as inp, target.open("xb") as out:
                    shutil.copyfileobj(inp, out)
                # Preserve executable scripts in legitimate Linux build contexts.
                if mode & 0o111:
                    target.chmod(0o755)
    if not (destination / "Dockerfile").is_file():
        raise ValueError("Dockerfile must be at ZIP root")


def restriction_failure(output):
    """Author-facing (code, command) from the restriction layer's build output."""
    matches = RESTRICTION_ERROR.findall(output or "")
    if not matches or matches[-1][0] not in FAILURE_CODES:
        return "RESTRICTION_FAILED", None
    code, arg = matches[-1]
    return code, arg if COMMAND.fullmatch(arg) else None


def report_failure(run, bucket, key, region, code, command):
    """Best effort: the build still fails with its original error if this upload fails."""
    body = {"version": 1, "code": code, **({"command": command} if command else {})}
    result_key = key.rsplit("/", 1)[0] + "/result.json"
    handle, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(body, stream)
        run(["aws", "s3api", "put-object", "--bucket", bucket, "--key", result_key, "--body", path,
             "--content-type", "application/json", "--region", region], check=True, capture_output=True, text=True)
    except Exception as error:
        print(f"DIVEON_RESULT_UPLOAD_FAILED {type(error).__name__}", flush=True)
    finally:
        os.unlink(path)


def echo(result):
    for stream in (getattr(result, "stdout", None), getattr(result, "stderr", None)):
        if isinstance(stream, str) and stream:
            print(stream, end="" if stream.endswith("\n") else "\n", flush=True)


def build_image(env, run=subprocess.run):
    key, bucket, region, registry, image = configuration(env)
    failure = {"code": None, "command": None}
    try:
        return _build(key, bucket, region, registry, image, run, failure)
    except Exception:
        if failure["code"]:
            report_failure(run, bucket, key, region, failure["code"], failure["command"])
        raise


def _build(key, bucket, region, registry, image, run, failure):
    with tempfile.TemporaryDirectory(prefix="diveon-build-") as temp:
        root = Path(temp)
        archive = root / "source.zip"
        context = root / "context"
        context.mkdir()
        # One request returns the exact object and the metadata the backend stored with it.
        response = run(["aws", "s3api", "get-object", "--bucket", bucket, "--key", key, "--region", region,
                        str(archive)], check=True, capture_output=True, text=True)
        head = json.loads(response.stdout)
        failure["code"] = "POLICY_INVALID"
        names = command_policy(head.get("Metadata", {}) if isinstance(head, dict) else None)
        failure["code"] = "SOURCE_INVALID"
        unpack(archive, context)
        failure["code"] = None  # Registry login problems are platform faults, not the author's.
        password = run(["aws", "ecr", "get-login-password", "--region", region],
                       check=True, capture_output=True, text=True).stdout
        run(["docker", "login", "--username", "AWS", "--password-stdin", registry],
            input=password, text=True, check=True)
        if names is None:
            failure["code"] = "DOCKERFILE_FAILED"
            run(["docker", "build", "-t", image, str(context)], check=True)
            # No post_build push or self-reported SQS success. Any failure aborts this phase.
            failure["code"] = "PUSH_FAILED"
            run(["docker", "push", image], check=True)
            return image
        source = "diveon-src:" + image.rsplit(":", 1)[1]
        marker = f"{image}-{policy_marker(names)}"
        failure["code"] = "DOCKERFILE_FAILED"
        run(["docker", "build", "-t", source, str(context)], check=True)
        layer = root / "restriction"
        restriction_layer(layer, source, names)
        failure["code"] = "RESTRICTION_FAILED"
        try:
            echo(run(["docker", "build", "--network", "none", "-t", image, "-t", marker, str(layer)],
                     check=True, capture_output=True, text=True))
        except subprocess.CalledProcessError as error:
            echo(error)
            failure["code"], failure["command"] = restriction_failure(f"{error.stdout or ''}\n{error.stderr or ''}")
            raise
        failure["code"] = "PUSH_FAILED"
        run(["docker", "push", image], check=True)
        # The backend marks the build READY only when this tag shares the image digest.
        run(["docker", "push", marker], check=True)
    return image


def main():
    build_image(os.environ)


if __name__ == "__main__":
    main()
