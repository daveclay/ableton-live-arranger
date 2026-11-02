#!/bin/bash
# Copy the LiveArranger Remote Script into Live's User Library, then ask a
# running instance to hot-reload its command handlers.
#
# A copy (not a symlink) is used because the User Library lives in Google Drive.
# Override the destination with LIVE_REMOTE_SCRIPTS=/path/to/Remote\ Scripts.
set -euo pipefail

DEST="${LIVE_REMOTE_SCRIPTS:-$HOME/Google Drive/Music/Abelton/User Library/User Library/Remote Scripts}"
SRC="$(cd "$(dirname "$0")/.." && pwd)/remote_script/LiveArranger"

mkdir -p "$DEST/LiveArranger"
rsync -a --delete --exclude __pycache__ --exclude logs "$SRC/" "$DEST/LiveArranger/"
echo "Installed to $DEST/LiveArranger"

# Hot-reload commands.py if Live is running the script (manager/server changes need a Live restart).
python3 - <<'EOF' || echo "(Live not reachable; restart Live / select LiveArranger as a Control Surface)"
import socket, json
s = socket.create_connection(("127.0.0.1", 11020), timeout=2)
s.sendall(b'{"id":1,"cmd":"reload"}\n')
print("Live:", json.loads(s.makefile().readline()).get("result"))
EOF
