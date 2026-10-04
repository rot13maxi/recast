#!/bin/bash
# Container entrypoint: boot the live image =
#   fresh base.image + kernel install (from JSON) + journal replay + queue REPL.
# Blocking: the queue loop keeps this process alive = container stays up.
set -e
PHARO=/opt/pharo/pharo-vm/pharo
IMAGE=${LIVE_IMAGE:-/opt/pharo/pharo.image}
DATA=${LIVE_DATA:-/data}
mkdir -p "$DATA/queue" "$DATA/heal" "$DATA/workspace"
chmod -R 777 "$DATA" || true

exec "$PHARO" --headless "$IMAGE" eval "$(cat /opt/pharo/kernel/boot_eval.st)"
