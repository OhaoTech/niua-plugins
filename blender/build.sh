#!/usr/bin/env bash
# Build a distributable NIUA Blender add-on zip.
# Output: dist/niua-blender-<version>.zip
#
# Usage: ./build.sh
set -euo pipefail

cd "$(dirname "$0")"

# Extract version from __init__.py bl_info
VERSION=$(python3 -c "
import re
with open('__init__.py') as f:
    m = re.search(r'\"version\":\s*\((\d+),\s*(\d+),\s*(\d+)\)', f.read())
print(f'{m[1]}.{m[2]}.{m[3]}')
")

# The zip filename is cosmetic (descriptive download name) but the
# folder INSIDE the zip must be a valid Python module name — Blender
# uses it as the import path. Hyphens and dots are illegal there.
MODULE="niua"
ZIPNAME="niua-blender-${VERSION}"
OUT="dist/${ZIPNAME}.zip"

mkdir -p dist
rm -rf "dist/${MODULE}" "$OUT"

mkdir "dist/${MODULE}"
cp __init__.py README.md "dist/${MODULE}/"

(cd dist && zip -qr "${ZIPNAME}.zip" "${MODULE}")
rm -rf "dist/${MODULE}"

echo "✓ Built $OUT"
echo "  Install via Blender → Edit → Preferences → Add-ons → Install..."
