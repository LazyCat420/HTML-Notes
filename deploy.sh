#!/bin/bash
# ============================================================
# HTML-Notes Engine — Build & Deploy to Synology NAS
#
# Thin wrapper — all logic lives in ../deploy-kit/lib.sh
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_NAME="html-notes"
DISPLAY_NAME="📝 HTML-Notes Engine"

source "${SCRIPT_DIR}/../deploy-kit/lib.sh"
