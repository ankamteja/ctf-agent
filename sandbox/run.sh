#!/usr/bin/env bash
# Launch a locked-down sandbox container for one challenge.
# Usage: run.sh <challenge_dir> [target_host] [target_port]
set -euo pipefail
CH="${1:?challenge dir}"; HOST="${2:-}"; PORT="${3:-}"
IMG=ctf-sandbox:1
podman run --rm -it \
  --name ctf-work-$$ \
  --network=slirp4netns \
  --memory=2g --cpus=2 --pids-limit=256 \
  --cap-drop=ALL --security-opt no-new-privileges \
  --read-only --tmpfs /tmp:rw,size=512m \
  -v "$CH":/work:ro,Z \
  -e TARGET_HOST="$HOST" -e TARGET_PORT="$PORT" \
  "$IMG" /bin/bash
