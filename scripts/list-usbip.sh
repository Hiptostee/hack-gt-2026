#!/usr/bin/env bash
set -euo pipefail

docker run --rm --privileged --pid=host alpine sh -lc '
  apk add --no-cache util-linux >/dev/null
  nsenter -t 1 -m -- usbip list -r host.docker.internal
'
