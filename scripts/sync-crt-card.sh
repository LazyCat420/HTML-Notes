#!/bin/bash
# The YouTube widget embeds the 3D CRT card. The card is authored in doc-client
# (apps/doc-client/static/crt-tv-player.html) and copied here so the widget is
# same-origin (the hub is deployed on a different host). Same rule as the
# walkman card: run this after every change to the doc-client card or the
# crt-tv glTF, then rebuild/deploy.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/../../doc-client/static" && pwd)"
DST="$(cd "$(dirname "$0")/.." && pwd)/app/static/crt"
mkdir -p "$DST/models"
cp "$SRC/model-viewer.min.js" "$DST/"
cp "$SRC/models/crt-tv-v1-lite.glb" "$SRC/models/studio.hdr" "$DST/models/"
sed -e 's#src="/static/model-viewer.min.js"#src="/static/crt/model-viewer.min.js"#' \
    -e 's#src="/static/models/crt-tv-v1-lite.glb"#src="/static/crt/models/crt-tv-v1-lite.glb"#' \
    -e 's#environment-image="/static/models/studio.hdr"#environment-image="/static/crt/models/studio.hdr"#' \
    "$SRC/crt-tv-player.html" > "$DST/player.html"
echo "synced CRT card → $DST/player.html"
