#!/usr/bin/env bash
# setup_models.sh — install wav2lip model and avatar data
# Usage: bash setup_models.sh
# Looks for files at /tmp/livetalking-models/wav2lip/ first.
# Falls back to Google Drive download via gdown if not found.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOCAL_CACHE="/tmp/livetalking-models/wav2lip"

# ── 1. Resolve source directory ───────────────────────────────────────
if [[ -f "$LOCAL_CACHE/wav2lip256.pth" && -f "$LOCAL_CACHE/wav2lip256_avatar1.tar.gz" ]]; then
    echo "[setup] Using cached files from $LOCAL_CACHE"
    SRC_DIR="$LOCAL_CACHE"
else
    echo "[setup] Cache not found — downloading from Google Drive..."
    if ! command -v gdown &>/dev/null; then
        echo "[setup] Installing gdown..."
        pip install -q gdown
    fi
    DOWNLOAD_DIR="$SCRIPT_DIR/_downloads"
    rm -rf "$DOWNLOAD_DIR"
    mkdir -p "$DOWNLOAD_DIR"
    GDRIVE_FOLDER="https://drive.google.com/drive/folders/1FOC_MD6wdogyyX_7V1d4NDIO7P9NlSAJ"
    gdown --folder "$GDRIVE_FOLDER" -O "$DOWNLOAD_DIR"
    SRC_DIR=$(find "$DOWNLOAD_DIR" -maxdepth 2 -name "wav2lip256.pth" -exec dirname {} \; | head -1)
    if [[ -z "$SRC_DIR" ]]; then
        echo "[setup] ERROR: wav2lip256.pth not found in downloaded folder" >&2
        exit 1
    fi
fi

# ── 2. Place model file ───────────────────────────────────────────────
mkdir -p models
cp "$SRC_DIR/wav2lip256.pth" models/wav2lip.pth
echo "[setup] ✓ Model copied → models/wav2lip.pth"

# ── 3. Extract avatar data ────────────────────────────────────────────
AVATAR_TAR="$SRC_DIR/wav2lip256_avatar1.tar.gz"
if [[ ! -f "$AVATAR_TAR" ]]; then
    echo "[setup] ERROR: wav2lip256_avatar1.tar.gz not found at $SRC_DIR" >&2
    exit 1
fi
mkdir -p data/avatars
tar -xzf "$AVATAR_TAR" -C data/avatars/
echo "[setup] ✓ Avatar extracted → data/avatars/wav2lip256_avatar1/"

# ── 4. Cleanup temp download if used ─────────────────────────────────
if [[ -d "$SCRIPT_DIR/_downloads" ]]; then
    rm -rf "$SCRIPT_DIR/_downloads"
fi

echo ""
echo "[setup] Done! Run LiveTalking with:"
echo "  python app.py --transport livekit \\"
echo "    --livekit_url wss://\$LIVEKIT_URL \\"
echo "    --livekit_token \$TOKEN \\"
echo "    --livekit_room \$LIVEKIT_ROOM \\"
echo "    --model wav2lip --avatar_id wav2lip256_avatar1"
