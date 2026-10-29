#!/bin/bash
# Render only. Installation/restart requires a separately approved deployment.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cat -- "$script_dir/ws-terminal.service"
