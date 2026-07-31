#!/usr/bin/env bash
# install-agent.sh — Install and start the OpenCLI Agent on a remote node.
#
# This script is served dynamically by the center API at:
#   GET /api/v1/nodes/install/agent.sh
# The center pre-fills CENTRAL_API_URL so you can install with one command:
#   curl -fsSL http://<center>:8031/api/v1/nodes/install/agent.sh | bash
#
# Manual usage:
#   CENTRAL_API_URL=http://192.168.1.1:8031 bash install-agent.sh [docker|python] [--install-chrome]
#
# Environment variables (override at runtime):
#   CENTRAL_API_URL    Center API base URL (required)
#   AGENT_API_TOKEN    Bearer token for center /api auth (optional unless center enforces it)
#   AGENT_REGISTER     Registration mode: http | ws (default: ws)
#   AGENT_PORT         Agent HTTP port (default: 19823)
#   AGENT_ADVERTISE_URL Canonical agent URL registered at center (optional; useful for overlay/tunnel IP/DNS)
#   AGENT_LABEL        Human-readable label (default: hostname)
#   AGENT_MODE         Chrome connection mode: cdp | bridge (default: cdp)
#   INSTALL_CHROME     Embed Chromium in Docker image: true | false (default: false)
#                      true  → uses image tag suffix "-chrome" (~450 MB, self-contained)
#                      false → uses base image (~100 MB), connect to host Chrome via CDP
#   HTTP_PROXY         HTTP proxy for agent → center (optional)
#   HTTPS_PROXY        HTTPS proxy for agent → center (optional)
#   IMAGE_TAG          Docker image tag (default: injected by center API)
#   OPENCLI_BROWSER_PROFILE_KIND authenticated | anonymous (default: authenticated)
#   FLEET_NETWORK_PROVIDER lan | netbird | wireguard | ssh | custom (default: injected by center API)
#   NETBIRD_MODE       off | host | docker (default: injected by center API)
#   NETBIRD_SETUP_KEY  Setup key used to enroll this node into NetBird
#   NETBIRD_MANAGEMENT_URL Self-hosted NetBird management URL (optional)
#   NETBIRD_IMAGE_TAG  NetBird Docker image tag (default: latest)
#   OHMYOPENCLI_REPO   Optional audited adapter-pack repository for Python installs
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Injected by center API ────────────────────────────────────────────────────
CENTRAL_API_URL="${CENTRAL_API_URL:-__CENTRAL_API_URL__}"
AGENT_API_TOKEN="${AGENT_API_TOKEN:-${API_AUTH_TOKEN:-__AGENT_API_TOKEN__}}"
FLEET_NETWORK_PROVIDER="${FLEET_NETWORK_PROVIDER:-__FLEET_NETWORK_PROVIDER__}"
NETBIRD_MODE="${NETBIRD_MODE:-__NETBIRD_MODE__}"
NETBIRD_SETUP_KEY="${NETBIRD_SETUP_KEY:-__NETBIRD_SETUP_KEY__}"
NETBIRD_MANAGEMENT_URL="${NETBIRD_MANAGEMENT_URL:-__NETBIRD_MANAGEMENT_URL__}"
NETBIRD_IMAGE_TAG="${NETBIRD_IMAGE_TAG:-__NETBIRD_IMAGE_TAG__}"

# ── Defaults ──────────────────────────────────────────────────────────────────
AGENT_REGISTER="${AGENT_REGISTER:-ws}"
AGENT_PORT="${AGENT_PORT:-19823}"
AGENT_ADVERTISE_URL="${AGENT_ADVERTISE_URL:-}"
AGENT_LABEL="${AGENT_LABEL:-$(hostname)}"
AGENT_MODE="${AGENT_MODE:-cdp}"
IMAGE_TAG="${IMAGE_TAG:-__IMAGE_TAG__}"
INSTALL_CHROME="${INSTALL_CHROME:-false}"
OPENCLI_BROWSER_PROFILE_KIND="${OPENCLI_BROWSER_PROFILE_KIND:-authenticated}"
OHMYOPENCLI_REPO="${OHMYOPENCLI_REPO:-}"
INSTALL_MODE="${1:-docker}"

[[ "$CENTRAL_API_URL" == "__CENTRAL_API_URL__" ]] && CENTRAL_API_URL=""
[[ "$AGENT_API_TOKEN" == "__AGENT_API_TOKEN__" ]] && AGENT_API_TOKEN=""
[[ "$FLEET_NETWORK_PROVIDER" == "__FLEET_NETWORK_PROVIDER__" ]] && FLEET_NETWORK_PROVIDER="lan"
[[ "$NETBIRD_MODE" == "__NETBIRD_MODE__" ]] && NETBIRD_MODE="off"
[[ "$NETBIRD_SETUP_KEY" == "__NETBIRD_SETUP_KEY__" ]] && NETBIRD_SETUP_KEY=""
[[ "$NETBIRD_MANAGEMENT_URL" == "__NETBIRD_MANAGEMENT_URL__" ]] && NETBIRD_MANAGEMENT_URL=""
[[ "$NETBIRD_IMAGE_TAG" == "__NETBIRD_IMAGE_TAG__" ]] && NETBIRD_IMAGE_TAG="latest"
[[ "$IMAGE_TAG" == "__IMAGE_TAG__" ]] && IMAGE_TAG="latest"

if [[ "$NETBIRD_MODE" == "off" && -n "$NETBIRD_SETUP_KEY" ]]; then
  NETBIRD_MODE="host"
fi

# Parse --install-chrome flag from any positional argument
for arg in "$@"; do
  case "$arg" in
    --install-chrome) INSTALL_CHROME=true ;;
    --no-chrome)      INSTALL_CHROME=false ;;
  esac
done

# Select image tag suffix based on Chrome preference
if [[ "$INSTALL_CHROME" == "true" ]]; then
  CHROME_SUFFIX="-chrome"
else
  CHROME_SUFFIX=""
fi
AGENT_IMAGE="${DOCKER_REGISTRY:-ghcr.io/}${DOCKER_IMAGE_NAMESPACE:-2233admin}/opencli-admin-agent:${IMAGE_TAG}${CHROME_SUFFIX}"

# ─────────────────────────────────────────────────────────────────────────────

info()  { printf '\e[32m[INFO]\e[0m  %s\n' "$*"; }
warn()  { printf '\e[33m[WARN]\e[0m  %s\n' "$*"; }
die()   { printf '\e[31m[ERROR]\e[0m %s\n' "$*" >&2; exit 1; }

[[ -z "$CENTRAL_API_URL" ]] && die "CENTRAL_API_URL is required"

AUTH_HEADER=()
if [[ -n "$AGENT_API_TOKEN" ]]; then
  AUTH_HEADER=(-H "Authorization: Bearer $AGENT_API_TOKEN")
fi

info "OpenCLI Agent Installer"
info "  Center:         $CENTRAL_API_URL"
info "  Register:       $AGENT_REGISTER"
info "  API Auth:       $([[ -n "$AGENT_API_TOKEN" ]] && echo configured || echo unset)"
info "  Port:           $AGENT_PORT"
info "  Advertise URL:  ${AGENT_ADVERTISE_URL:-auto}"
info "  Label:          $AGENT_LABEL"
info "  Mode:           $INSTALL_MODE"
info "  Agent Mode:     $AGENT_MODE"
info "  Install Chrome: $INSTALL_CHROME"
info "  Image:          $AGENT_IMAGE"
info "  Fleet Network:  $FLEET_NETWORK_PROVIDER"
info "  NetBird Mode:   $NETBIRD_MODE"
echo

# ── Fleet network reachability ────────────────────────────────────────────────
case "$FLEET_NETWORK_PROVIDER" in
  lan|netbird|wireguard|ssh|custom) ;;
  *) die "Unknown FLEET_NETWORK_PROVIDER '$FLEET_NETWORK_PROVIDER'. Use lan, netbird, wireguard, ssh, or custom." ;;
esac

run_netbird() {
  if netbird "$@"; then
    return 0
  fi
  if command -v sudo >/dev/null 2>&1; then
    sudo netbird "$@"
  else
    return 1
  fi
}

install_netbird_host() {
  [[ -n "$NETBIRD_SETUP_KEY" ]] || die "NETBIRD_SETUP_KEY is required when NETBIRD_MODE=host"

  if ! command -v netbird >/dev/null 2>&1; then
    command -v curl >/dev/null 2>&1 || die "curl is required to install NetBird"
    info "Installing NetBird client on host..."
    if [[ "$(id -u)" == "0" ]]; then
      curl -fsSL https://pkgs.netbird.io/install.sh | sh
    elif command -v sudo >/dev/null 2>&1; then
      curl -fsSL https://pkgs.netbird.io/install.sh | sudo sh
    else
      die "sudo is required to install NetBird on this host"
    fi
  fi

  local up_args=(up --setup-key "$NETBIRD_SETUP_KEY")
  [[ -n "$NETBIRD_MANAGEMENT_URL" ]] && up_args+=(--management-url "$NETBIRD_MANAGEMENT_URL")

  info "Bringing NetBird up on host..."
  run_netbird "${up_args[@]}" || die "netbird up failed"
  run_netbird status || warn "netbird status failed; continuing after successful up"
}

install_netbird_docker() {
  [[ -n "$NETBIRD_SETUP_KEY" ]] || die "NETBIRD_SETUP_KEY is required when NETBIRD_MODE=docker"
  command -v docker >/dev/null 2>&1 || die "Docker is required for NETBIRD_MODE=docker"

  local container_name="opencli-netbird"
  if docker ps -a --format '{{.Names}}' | grep -qx "$container_name"; then
    warn "Existing container '$container_name' found — removing..."
    docker rm -f "$container_name" >/dev/null
  fi

  local netbird_env=(-e NB_SETUP_KEY="$NETBIRD_SETUP_KEY")
  [[ -n "$NETBIRD_MANAGEMENT_URL" ]] && netbird_env+=(-e NB_MANAGEMENT_URL="$NETBIRD_MANAGEMENT_URL")

  info "Starting NetBird client container '$container_name'..."
  docker run -d \
    --name "$container_name" \
    --restart unless-stopped \
    --network host \
    --privileged \
    "${netbird_env[@]}" \
    -v netbird-client:/var/lib/netbird \
    "netbirdio/netbird:${NETBIRD_IMAGE_TAG}"
}

install_netbird() {
  case "$NETBIRD_MODE" in
    off)
      if [[ "$FLEET_NETWORK_PROVIDER" == "netbird" ]]; then
        warn "FLEET_NETWORK_PROVIDER=netbird but NETBIRD_MODE=off and no setup key was provided"
      fi
      ;;
    host) install_netbird_host ;;
    docker) install_netbird_docker ;;
    *) die "Unknown NETBIRD_MODE '$NETBIRD_MODE'. Use off, host, or docker." ;;
  esac
}

install_fleet_network() {
  case "$FLEET_NETWORK_PROVIDER" in
    netbird)
      install_netbird
      ;;
    lan)
      ;;
    wireguard)
      info "WireGuard provider selected; assuming the WireGuard interface is already up."
      [[ -n "$AGENT_ADVERTISE_URL" ]] || warn "Set AGENT_ADVERTISE_URL to the WireGuard-reachable agent URL when center HTTP callbacks are required."
      ;;
    ssh)
      info "SSH provider selected; assuming the SSH tunnel is already established."
      [[ -n "$AGENT_ADVERTISE_URL" ]] || warn "Set AGENT_ADVERTISE_URL to the forwarded agent URL when center HTTP callbacks are required."
      ;;
    custom)
      info "Custom network provider selected; assuming reachability is managed outside this installer."
      [[ -n "$AGENT_ADVERTISE_URL" ]] || warn "Set AGENT_ADVERTISE_URL to the center-reachable agent URL when center HTTP callbacks are required."
      ;;
  esac
}

# ── Docker install ─────────────────────────────────────────────────────────────
install_docker() {
  command -v docker >/dev/null 2>&1 || die "Docker is not installed. Visit https://docs.docker.com/get-docker/"

  CONTAINER_NAME="opencli-agent"

  # Stop and remove existing container with same name
  if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    warn "Existing container '$CONTAINER_NAME' found — removing..."
    docker rm -f "$CONTAINER_NAME" >/dev/null
  fi

  # Auto-find a free port starting from AGENT_PORT
  ORIG_PORT="$AGENT_PORT"
  while docker ps --format '{{.Ports}}' | grep -q "0.0.0.0:${AGENT_PORT}->"; do
    AGENT_PORT=$(( AGENT_PORT + 1 ))
  done
  [[ "$AGENT_PORT" != "$ORIG_PORT" ]] && warn "Port $ORIG_PORT in use, using $AGENT_PORT instead"

  # Inside Docker, 'localhost'/'127.0.0.1' refers to the container itself, not the host.
  # Translate to host.docker.internal (works on Docker Desktop + Linux with --add-host).
  DOCKER_CENTRAL_URL=$(echo "$CENTRAL_API_URL" | sed 's|localhost|host.docker.internal|g; s|127\.0\.0\.1|host.docker.internal|g')
  [[ "$DOCKER_CENTRAL_URL" != "$CENTRAL_API_URL" ]] && warn "Docker networking: using $DOCKER_CENTRAL_URL inside container"

  PROXY_ARGS=""
  [[ -n "${HTTP_PROXY:-}" ]]  && PROXY_ARGS="$PROXY_ARGS -e HTTP_PROXY=$HTTP_PROXY"
  [[ -n "${HTTPS_PROXY:-}" ]] && PROXY_ARGS="$PROXY_ARGS -e HTTPS_PROXY=$HTTPS_PROXY"

  info "Starting container '$CONTAINER_NAME'..."
  # shellcheck disable=SC2086
  # --add-host makes host.docker.internal work on Linux (no-op on Docker Desktop)
  docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    --add-host=host.docker.internal:host-gateway \
    -e CENTRAL_API_URL="$DOCKER_CENTRAL_URL" \
    -e AGENT_REGISTER="$AGENT_REGISTER" \
    -e AGENT_PORT="$AGENT_PORT" \
    -e AGENT_ADVERTISE_URL="$AGENT_ADVERTISE_URL" \
    -e AGENT_LABEL="$AGENT_LABEL" \
    -e AGENT_MODE="${AGENT_MODE}" \
    -e AGENT_API_TOKEN="$AGENT_API_TOKEN" \
    -e AGENT_DEPLOY_TYPE="docker" \
    -e OPENCLI_BROWSER_PROFILE_KIND="$OPENCLI_BROWSER_PROFILE_KIND" \
    $PROXY_ARGS \
    -p "${AGENT_PORT}:${AGENT_PORT}" \
    "$AGENT_IMAGE"

  info "Container started. Waiting for registration..."
  sleep 3
  if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
    info "Agent is running!"
  else
    die "Container failed to start. Check: docker logs $CONTAINER_NAME"
  fi

}

# ── Python/pip install ─────────────────────────────────────────────────────────
install_python() {
  command -v python3 >/dev/null 2>&1 || die "Python 3 is not installed"

  AGENT_DIR="$HOME/.opencli-agent"
  mkdir -p "$AGENT_DIR/backend"

  # ── Download agent_server.py from center ──────────────────────────────────
  info "Downloading agent_server.py from center..."
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "${AUTH_HEADER[@]}" \
      "$CENTRAL_API_URL/api/v1/nodes/install/agent_server.py" \
      -o "$AGENT_DIR/backend/agent_server.py"
  elif command -v wget >/dev/null 2>&1; then
    if [[ -n "$AGENT_API_TOKEN" ]]; then
      wget --header="Authorization: Bearer $AGENT_API_TOKEN" \
        -qO "$AGENT_DIR/backend/agent_server.py" \
        "$CENTRAL_API_URL/api/v1/nodes/install/agent_server.py"
    else
      wget -qO "$AGENT_DIR/backend/agent_server.py" \
        "$CENTRAL_API_URL/api/v1/nodes/install/agent_server.py"
    fi
  else
    die "Neither curl nor wget found — cannot download agent_server.py"
  fi
  touch "$AGENT_DIR/backend/__init__.py"

  # ── Install Python dependencies ───────────────────────────────────────────
  info "Installing Python dependencies..."
  VENV_DIR="$AGENT_DIR/venv"
  if python3 -m venv "$VENV_DIR" 2>/dev/null; then
    "$VENV_DIR/bin/pip" install --quiet fastapi "uvicorn[standard]" httpx pyyaml websockets
    UVICORN_BIN="$VENV_DIR/bin/uvicorn"
    info "Installed into virtualenv: $VENV_DIR"
  elif python3 -m pip install --user --quiet fastapi "uvicorn[standard]" httpx pyyaml websockets 2>/dev/null; then
    UVICORN_BIN="uvicorn"
  else
    die "Could not install Python dependencies. Try: python3 -m venv ~/.opencli-agent/venv && source ~/.opencli-agent/venv/bin/activate && pip install fastapi 'uvicorn[standard]' httpx pyyaml websockets"
  fi

  # ── Check / install opencli ───────────────────────────────────────────────
  if command -v npm >/dev/null 2>&1; then
    if [ -t 0 ]; then
      read -r -p "Install or upgrade managed OpenCLI 1.8.5 via npm? [Y/n] " _reply </dev/tty || _reply="Y"
    else
      _reply="Y"
      info "Installing pinned OpenCLI 1.8.5 via npm (non-interactive)..."
    fi
    if [[ "${_reply:-Y}" =~ ^[Yy]$ ]]; then
      npm install -g @jackwener/opencli@1.8.5
      PATCH_FILE="$AGENT_DIR/patch-opencli.js"
      curl -fsSL "${AUTH_HEADER[@]}" \
        "$CENTRAL_API_URL/api/v1/nodes/install/patch-opencli.js" -o "$PATCH_FILE"
      node "$PATCH_FILE"
      info "opencli: $(opencli --version 2>/dev/null | head -1 || echo 'installed')"
    else
      warn "Skipped — opencli channel will be unavailable"
    fi
  else
    warn "npm not found — opencli channel will be unavailable"
    warn "  Install Node.js 22+ from https://nodejs.org then run: npm install -g @jackwener/opencli@1.8.5"
  fi

  # Organization-specific adapter packs are optional and never fetched implicitly.
  OHMYOPENCLI_ROOT=""
  if [[ -n "$OHMYOPENCLI_REPO" ]]; then
    OHMYOPENCLI_ROOT="$AGENT_DIR/ohmyopencli"
    OHMYOPENCLI_COMMIT="${OHMYOPENCLI_COMMIT:-73cc60c83586ef2c95469b3b70d6cfc80fa5bc53}"
    OFFICIAL_SITE_CAPABILITY_COMMIT="${OFFICIAL_SITE_CAPABILITY_COMMIT:-$OHMYOPENCLI_COMMIT}"
    command -v git >/dev/null 2>&1 || die "git is required to install the adapter pack"
    [[ -e "$OHMYOPENCLI_ROOT" ]] && die \
      "Adapter-pack target already exists; archive it explicitly before reinstalling: $OHMYOPENCLI_ROOT"
    git clone "$OHMYOPENCLI_REPO" "$OHMYOPENCLI_ROOT"
    git -C "$OHMYOPENCLI_ROOT" checkout --detach "$OHMYOPENCLI_COMMIT"
    git -C "$OHMYOPENCLI_ROOT" merge-base --is-ancestor \
      "$OFFICIAL_SITE_CAPABILITY_COMMIT" HEAD
    (cd "$OHMYOPENCLI_ROOT" && npm ci && npm run bootstrap)
  fi

  # ── Find Chrome binary ────────────────────────────────────────────────────
  find_chrome() {
    local candidates=(
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
      "/Applications/Chromium.app/Contents/MacOS/Chromium"
      "$(command -v google-chrome-stable 2>/dev/null || true)"
      "$(command -v google-chrome 2>/dev/null || true)"
      "$(command -v chromium 2>/dev/null || true)"
      "$(command -v chromium-browser 2>/dev/null || true)"
    )
    for c in "${candidates[@]}"; do
      [[ -n "$c" && ( -f "$c" || -x "$c" ) ]] && { echo "$c"; return 0; }
    done
    return 1
  }

  # ── Start Chrome in CDP mode ──────────────────────────────────────────────
  CDP_PORT="${CDP_PORT:-9222}"
  CHROME_PROFILE="$AGENT_DIR/chrome-profile"
  if [[ "$OPENCLI_BROWSER_PROFILE_KIND" == "anonymous" ]]; then
    CHROME_PROFILE="$(mktemp -d /tmp/opencli-anonymous-profile.XXXXXX)"
  fi

  if [[ "${AGENT_MODE}" == "cdp" ]]; then
    if CHROME_BIN="$(find_chrome)"; then
      mkdir -p "$CHROME_PROFILE"
      CHROME_LOG="/tmp/opencli-chrome.log"
      # Remove stale profile locks left by previous crashes
      find "$CHROME_PROFILE" -name 'SingletonLock' -o -name 'SingletonCookie' -o -name 'SingletonSocket' \
        2>/dev/null | xargs rm -f 2>/dev/null || true
      CHROME_EXTRA_FLAGS=()
      if [[ "$(id -u)" == "0" ]]; then
        CHROME_EXTRA_FLAGS+=("--no-sandbox")
      fi
      if [[ -z "${DISPLAY:-}" ]]; then
        CHROME_EXTRA_FLAGS+=("--headless=new" "--disable-gpu")
      fi
      info "Starting Chrome (CDP :$CDP_PORT)  profile: $CHROME_PROFILE"
      nohup "$CHROME_BIN" \
        --remote-debugging-port="$CDP_PORT" \
        --remote-debugging-address=127.0.0.1 \
        --remote-allow-origins='*' \
        --user-data-dir="$CHROME_PROFILE" \
        --no-first-run \
        --no-default-browser-check \
        --window-size=1280,900 \
        "${CHROME_EXTRA_FLAGS[@]}" \
        about:blank >"$CHROME_LOG" 2>&1 &
      CHROME_PID=$!
      sleep 1
      if kill -0 "$CHROME_PID" 2>/dev/null; then
        info "Chrome started (pid $CHROME_PID)  log: $CHROME_LOG"
        export OPENCLI_CDP_ENDPOINT="http://127.0.0.1:$CDP_PORT"
      else
        warn "Chrome failed to start — see $CHROME_LOG"
      fi
    else
      warn "Chrome/Chromium not found — CDP mode may fail"
      warn "  macOS: install Google Chrome or Chromium"
      warn "  Linux: apt install chromium-browser"
    fi
    export OPENCLI_CDP_ENDPOINT="${OPENCLI_CDP_ENDPOINT:-http://127.0.0.1:$CDP_PORT}"
  fi

  # ── Launch agent server ───────────────────────────────────────────────────
  AGENT_CMD="$UVICORN_BIN backend.agent_server:app --host 0.0.0.0 --port ${AGENT_PORT}"
  SYSTEMD_UNIT="/etc/systemd/system/opencli-agent.service"

  if command -v systemctl >/dev/null 2>&1 && [[ -w /etc/systemd/system ]]; then
    info "Installing systemd service..."
    cat > "$SYSTEMD_UNIT" <<EOF
[Unit]
Description=OpenCLI Agent Server
After=network.target

[Service]
Type=simple
WorkingDirectory=${AGENT_DIR}
Restart=on-failure
RestartSec=5
Environment=CENTRAL_API_URL=${CENTRAL_API_URL}
Environment=AGENT_REGISTER=${AGENT_REGISTER}
Environment=AGENT_PORT=${AGENT_PORT}
Environment=AGENT_ADVERTISE_URL=${AGENT_ADVERTISE_URL}
Environment=AGENT_LABEL=${AGENT_LABEL}
Environment=AGENT_MODE=${AGENT_MODE}
Environment=AGENT_API_TOKEN=${AGENT_API_TOKEN}
Environment=AGENT_DEPLOY_TYPE=shell
$([ -n "$OHMYOPENCLI_ROOT" ] && echo "Environment=OHMYOPENCLI_ROOT=${OHMYOPENCLI_ROOT}")
Environment=OPENCLI_BROWSER_PROFILE_KIND=${OPENCLI_BROWSER_PROFILE_KIND}
$([ -n "${OPENCLI_CDP_ENDPOINT:-}" ] && echo "Environment=OPENCLI_CDP_ENDPOINT=${OPENCLI_CDP_ENDPOINT}")
$([ -n "${HTTP_PROXY:-}" ]  && echo "Environment=HTTP_PROXY=${HTTP_PROXY}")
$([ -n "${HTTPS_PROXY:-}" ] && echo "Environment=HTTPS_PROXY=${HTTPS_PROXY}")
ExecStart=${AGENT_CMD}

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --now opencli-agent
    info "Service enabled and started (systemctl status opencli-agent)"
  else
    info "Starting agent in background (no systemd)..."
    (
      cd "$AGENT_DIR"
      export CENTRAL_API_URL AGENT_REGISTER AGENT_PORT AGENT_ADVERTISE_URL AGENT_LABEL AGENT_MODE
      export AGENT_API_TOKEN
      export AGENT_DEPLOY_TYPE=shell
      export OPENCLI_BROWSER_PROFILE_KIND
      [[ -n "$OHMYOPENCLI_ROOT" ]] && export OHMYOPENCLI_ROOT
      [[ -n "${OPENCLI_CDP_ENDPOINT:-}" ]] && export OPENCLI_CDP_ENDPOINT
      [[ -n "${HTTP_PROXY:-}" ]]  && export HTTP_PROXY
      [[ -n "${HTTPS_PROXY:-}" ]] && export HTTPS_PROXY
      # shellcheck disable=SC2086
      nohup $AGENT_CMD > /tmp/opencli-agent.log 2>&1 &
      echo $! > /tmp/opencli-agent.pid
    )
    info "Agent started (PID=$(cat /tmp/opencli-agent.pid 2>/dev/null || echo '?')). Logs: /tmp/opencli-agent.log"
  fi
}

# ── Dispatch ───────────────────────────────────────────────────────────────────
install_fleet_network

case "$INSTALL_MODE" in
  docker) install_docker ;;
  python) install_python ;;
  *)      die "Unknown install mode '$INSTALL_MODE'. Usage: $0 [docker|python]" ;;
esac

echo
info "Done! The agent will register itself at: $CENTRAL_API_URL"
info "View registered nodes at: $CENTRAL_API_URL → 节点管理"
