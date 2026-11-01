#!/bin/bash
# The mini music player widget embeds the 3D walkman card. The card is authored
# in doc-client (apps/doc-client/static/walkman-player.html) and copied here so
# the widget is same-origin: the html-notes app is deployed on a different host
# from the hub, and a hostname-derived URL would point at the wrong box.
#
# Run this after every change to the doc-client card, then rebuild/deploy.
set -euo pipefail
SRC="$(cd "$(dirname "$0")/../../doc-client/static" && pwd)"
DST="$(cd "$(dirname "$0")/.." && pwd)/app/static/walkman"
mkdir -p "$DST/models"
cp "$SRC/model-viewer.min.js" "$DST/"
cp "$SRC/models/player-body-v3-lite.glb" "$SRC/models/studio.hdr" "$DST/models/"
sed -e 's#src="/static/model-viewer.min.js"#src="/static/walkman/model-viewer.min.js"#' \
    -e 's#src="/static/models/player-body-v3-lite.glb"#src="/static/walkman/models/player-body-v3-lite.glb"#' \
    -e 's#environment-image="/static/models/studio.hdr"#environment-image="/static/walkman/models/studio.hdr"#' \
    "$SRC/walkman-player.html" > "$DST/player.html"
echo "synced walkman card → $DST/player.html"
