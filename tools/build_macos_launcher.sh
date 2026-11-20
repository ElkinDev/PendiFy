#!/bin/bash
# Builds src/pendify/macos/PendiFy, the universal (arm64 and x86_64) main executable of PendiFy.app, from
# tools/macos_launcher.c. Needs the Xcode command line tools; only a maintainer runs it, install.sh copies the result.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p src/pendify/macos
clang -Os -Wall -Wextra -arch arm64 -arch x86_64 -mmacosx-version-min=11.0 \
    -o src/pendify/macos/PendiFy tools/macos_launcher.c
strip -x src/pendify/macos/PendiFy
lipo -info src/pendify/macos/PendiFy
