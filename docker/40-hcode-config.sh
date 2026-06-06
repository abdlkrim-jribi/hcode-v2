#!/bin/sh
# nginx:alpine runs every executable /docker-entrypoint.d/*.sh before starting
# nginx. This hook materializes the runtime config from environment variables
# and wires it into the served HTML — so one static image targets any backend.
set -e

HTML_DIR=/usr/share/nginx/html
TEMPLATE=/opt/hcode/config.js.template

# Defaults: empty → bridge.ts falls through to build-time env, then to mock.
export HCODE_WS_URL="${HCODE_WS_URL:-}"
export VITE_MOCK="${VITE_MOCK:-}"

# Render config.js, substituting only our two known vars.
envsubst '${HCODE_WS_URL} ${VITE_MOCK}' < "$TEMPLATE" > "$HTML_DIR/config.js"

# Inject the <script src="/config.js"> tag into <head> once (idempotent), so it
# executes before the app bundle. The built index.html source is left untouched.
if ! grep -q 'src="/config.js"' "$HTML_DIR/index.html"; then
    sed -i 's#</head>#    <script src="/config.js"></script>\n  </head>#' "$HTML_DIR/index.html"
fi

echo "[hcode-config] HCODE_WS_URL='${HCODE_WS_URL}' VITE_MOCK='${VITE_MOCK}'"
