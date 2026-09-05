#!/bin/bash
set -e

rm -f /tmp/.X99-lock
Xvfb :99 -screen 0 1280x900x24 -nolisten tcp &
export DISPLAY=:99
sleep 1

find /home/chrome/.config/chromium -name 'SingletonLock' -o -name 'SingletonCookie' -o -name 'SingletonSocket' 2>/dev/null | xargs rm -f 2>/dev/null || true

export CHROME_HOSTNAME="${CHROME_HOSTNAME:-${HOSTNAME:-chrome}}"
envsubst '${CHROME_HOSTNAME}' < /etc/nginx/conf.d/cdp.conf.template > /etc/nginx/conf.d/cdp.conf
nginx -g 'daemon off;' &
x11vnc -display :99 -nopw -listen 0.0.0.0 -xkb -forever -shared &
websockify --web /usr/share/novnc 6080 localhost:5900 &

DAEMON_JS="$(npm root -g)/@jackwener/opencli/dist/src/daemon.js"
if [ -f "$DAEMON_JS" ]; then
  (while true; do
    OPENCLI_DAEMON_LISTEN=0.0.0.0 node "$DAEMON_JS"
    echo "[entrypoint] Browser Bridge daemon exited, restarting in 1s..."
    sleep 1
  done) &
  echo "[entrypoint] Browser Bridge daemon started on 0.0.0.0:${OPENCLI_DAEMON_PORT:-19825}"
else
  echo "[entrypoint] WARNING: Browser Bridge daemon not found at $DAEMON_JS"
fi

# The profile is writable user/site state only. Every capability comes from a
# read-only, versioned runtime bundle and Chromium is given exactly the
# allowlisted extension directories below.
BROWSER_RUNTIME_BUNDLE_ROOT="${BROWSER_RUNTIME_BUNDLE_ROOT:-/opt/browser-runtime-bundles}"
BROWSER_RUNTIME_BUNDLE_MANIFEST="${BROWSER_RUNTIME_BUNDLE_MANIFEST:-$BROWSER_RUNTIME_BUNDLE_ROOT/opencli-default/2/manifest.json}"
BUNDLE_EXTENSION_OUTPUT="$(node /usr/local/bin/resolve-browser-runtime-bundle.mjs "$BROWSER_RUNTIME_BUNDLE_MANIFEST" "$BROWSER_RUNTIME_BUNDLE_ROOT")"
BUNDLE_RUNTIME_REPORT="$(node /usr/local/bin/resolve-browser-runtime-bundle.mjs "$BROWSER_RUNTIME_BUNDLE_MANIFEST" "$BROWSER_RUNTIME_BUNDLE_ROOT" --report)"
read_manifest_component_version() {
  node -e 'const manifest=JSON.parse(require("fs").readFileSync(process.argv[1],"utf8")); const component=manifest.components.find((item)=>item.id===process.argv[2]); if(component) process.stdout.write(component.version);' "$BROWSER_RUNTIME_BUNDLE_MANIFEST" "$1"
}
SCRIPT_HOST_VERSION="$(read_manifest_component_version opencli-script-host)"
VIOLENTMONKEY_VERSION="$(read_manifest_component_version violentmonkey)"

BUNDLE_EXTENSION_DIRS=()
if [ -n "$BUNDLE_EXTENSION_OUTPUT" ]; then
  mapfile -t BUNDLE_EXTENSION_DIRS <<< "$BUNDLE_EXTENSION_OUTPUT"
fi
CHROME_EXTRA_FLAGS=(--disable-extensions)
if [ "${#BUNDLE_EXTENSION_DIRS[@]}" -gt 0 ]; then
  EXTENSION_DIRS="$(IFS=,; echo "${BUNDLE_EXTENSION_DIRS[*]}")"
  CHROME_EXTRA_FLAGS=("--disable-extensions-except=$EXTENSION_DIRS" "--load-extension=$EXTENSION_DIRS")
  echo "[entrypoint] Runtime bundle loaded from $BROWSER_RUNTIME_BUNDLE_MANIFEST"
fi
NETWORK_MODE="$(node -e 'const policy=JSON.parse(process.argv[1]||"{\"mode\":\"direct\"}"); if(!["direct","restricted"].includes(policy.mode)) process.exit(1); process.stdout.write(policy.mode);' "${BROWSER_NETWORK_POLICY:-}")"
if [ "$NETWORK_MODE" = "restricted" ]; then
  CHROME_EXTRA_FLAGS+=("--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost")
fi
STARTUP_PAGES=()
if [ -n "${BROWSER_STARTUP_PAGES:-}" ]; then
  STARTUP_PAGE_OUTPUT="$(node -e 'const pages=JSON.parse(process.argv[1]); if(!Array.isArray(pages)||pages.length>10||pages.some((item)=>typeof item!=="string"||!/^https?:\/\//.test(item))) process.exit(1); process.stdout.write(pages.join("\n"));' "$BROWSER_STARTUP_PAGES")"
  if [ -n "$STARTUP_PAGE_OUTPUT" ]; then mapfile -t STARTUP_PAGES <<< "$STARTUP_PAGE_OUTPUT"; fi
fi

BROWSER_ENGINE="${BROWSER_ENGINE-chromium}"
CHROME_BIN="$(node /usr/local/bin/resolve-browser-executable.mjs "$BROWSER_ENGINE")" || {
  echo "[entrypoint] Browser engine resolution failed for $BROWSER_ENGINE" >&2
  exit 1
}
echo "[entrypoint] Browser engine: $BROWSER_ENGINE"
start_chrome() {
  find /home/chrome/.config/chromium -name 'SingletonLock' -o -name 'SingletonCookie' -o -name 'SingletonSocket' 2>/dev/null | xargs rm -f 2>/dev/null || true
  "$CHROME_BIN" --remote-debugging-port=9222 --remote-debugging-address=0.0.0.0 --remote-allow-origins='*' --no-sandbox --disable-dev-shm-usage --user-data-dir=/home/chrome/.config/chromium --window-size=1280,900 "${CHROME_EXTRA_FLAGS[@]}" "$@"
}

run_runtime_self_check() {
  for _ in $(seq 1 30); do
    if curl -sf http://localhost:9222/json/version >/dev/null 2>&1; then
      EXTENSION_WORKERS="$(curl -sf http://localhost:9222/json/list | node -e 'let data=""; process.stdin.on("data",(chunk)=>data+=chunk); process.stdin.on("end",()=>{const targets=JSON.parse(data); process.stdout.write(String(targets.filter((target)=>target.type==="service_worker"&&target.url.startsWith("chrome-extension://")).length));});')"
      if [ "$EXTENSION_WORKERS" -ge "${#BUNDLE_EXTENSION_DIRS[@]}" ] && { [ -z "$SCRIPT_HOST_VERSION" ] || node /usr/local/bin/ensure-script-host.mjs http://localhost:9222 >/dev/null; }; then
        READY_RUNTIME_REPORT="$BUNDLE_RUNTIME_REPORT"
        if [ -n "$VIOLENTMONKEY_VERSION" ]; then
          USER_SCRIPTS_ACCESS_CHECK="$(node /usr/local/bin/ensure-violentmonkey-userscripts-access.mjs http://localhost:9222 "$VIOLENTMONKEY_VERSION")" || { sleep 1; continue; }
          READY_RUNTIME_REPORT="$(node -e 'const report=JSON.parse(process.argv[1]); const check=JSON.parse(process.argv[2]); report.self_check={...report.self_check,violentmonkey_user_scripts_access:check}; process.stdout.write(JSON.stringify(report));' "$BUNDLE_RUNTIME_REPORT" "$USER_SCRIPTS_ACCESS_CHECK")"
        fi
        printf '%s\n' "$READY_RUNTIME_REPORT" > /tmp/browser-runtime-report.json
        return 0
      fi
    fi
    sleep 1
  done
  echo "[entrypoint] Chromium did not become ready; runtime report not written" >&2
  return 1
}

while true; do
  # A report is only valid for the Chromium process that produced it.
  rm -f /tmp/browser-runtime-report.json
  start_chrome "${STARTUP_PAGES[@]}" &
  CHROME_PID=$!
  run_runtime_self_check &
  CHECK_PID=$!
  wait "$CHROME_PID" || true
  kill "$CHECK_PID" 2>/dev/null || true
  wait "$CHECK_PID" 2>/dev/null || true
  rm -f /tmp/browser-runtime-report.json
  echo "[entrypoint] Chromium exited, restarting in 2s..."
  sleep 2
done
