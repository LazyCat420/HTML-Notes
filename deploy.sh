#!/bin/bash
# ============================================================
# HTML-Notes Engine — Build & Deploy to Synology NAS
#
# Thin wrapper — all logic lives in ../deploy-kit/lib.sh
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_NAME="html-notes"
DISPLAY_NAME="📝 HTML-Notes Engine"

# The deep-research route imports lazycat.research from the shared SDK, which is
# volume-mounted (../lazycat-sdk) rather than baked into the image. Ship it to the
# NAS as a sibling of the compose dir so the mount resolves — same as
# trading-service/deploy.sh.
EXTRA_SSH_SYNC() {
  info "Syncing lazycat-sdk to remote host..."
  tar --exclude='lazycat-sdk/.venv' --exclude='lazycat-sdk/__pycache__' \
    -czC "${SCRIPT_DIR}/../" lazycat-sdk \
    | ssh "$DEPLOY_SSH_HOST" "sudo mkdir -p '${DEPLOY_COMPOSE_ROOT}/lazycat-sdk' && sudo tar -xzC '${DEPLOY_COMPOSE_ROOT}'"
}

source "${SCRIPT_DIR}/../deploy-kit/lib.sh"
