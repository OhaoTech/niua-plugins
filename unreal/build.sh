#!/usr/bin/env bash
# Build a distributable NIUA Unreal Engine plugin zip.
# Output: dist/niua-unreal-<version>.zip
#
# The zip contains a single top-level NIUA/ folder so users can drop it
# straight into <Project>/Plugins/ and have UE pick it up at next launch.
#
# Usage: ./build.sh
set -euo pipefail

cd "$(dirname "$0")"

# Extract VersionName from NIUA.uplugin (single source of truth)
VERSION=$(python3 -c "
import json
with open('NIUA.uplugin') as f:
    print(json.load(f)['VersionName'])
")

# UE plugin folder name = name in 'Plugins/' under the user's project.
# Matches the .uplugin filename so UE associates them correctly.
PLUGIN="NIUA"
ZIPNAME="niua-unreal-${VERSION}"
OUT="dist/${ZIPNAME}.zip"

mkdir -p dist
rm -rf "dist/${PLUGIN}" "$OUT"

mkdir "dist/${PLUGIN}"
cp NIUA.uplugin README.md "dist/${PLUGIN}/"
cp -r Content "dist/${PLUGIN}/"

(cd dist && zip -qr "${ZIPNAME}.zip" "${PLUGIN}")
rm -rf "dist/${PLUGIN}"

echo "✓ Built $OUT"
echo "  Install: unzip into <YourProject>/Plugins/ so the layout becomes"
echo "           <YourProject>/Plugins/NIUA/NIUA.uplugin"
