#!/usr/bin/env sh
set -eu

VERSION="${OPENCLI_ADMIN_VERSION:-0.4.0}"
REPOSITORY="${OPENCLI_ADMIN_REPOSITORY:-2233admin/opencli-admin}"
INSTALL_DIR="${OPENCLI_ADMIN_DIR:-$PWD/opencli-admin}"

for command_name in docker curl tar; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "Missing required command: $command_name" >&2
    exit 1
  }
done
docker compose version >/dev/null
docker info >/dev/null

if [ -d "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
  echo "Install directory is not empty: $INSTALL_DIR" >&2
  exit 1
fi

mkdir -p "$INSTALL_DIR"
archive="$(mktemp)"
trap 'rm -f "$archive"' EXIT
curl -fsSL "https://github.com/${REPOSITORY}/archive/refs/tags/v${VERSION}.tar.gz" -o "$archive"
tar -xzf "$archive" --strip-components=1 -C "$INSTALL_DIR"
cp "$INSTALL_DIR/.env.docker.example" "$INSTALL_DIR/.env"

random_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$1"
  else
    docker run --rm python:3.13-alpine python -c \
      "import secrets; print(secrets.token_hex($1))"
  fi
}

random_fernet() {
  if command -v openssl >/dev/null 2>&1; then
    raw="$(openssl rand -base64 32)" || return 1
    if [ -z "$raw" ]; then
      echo "openssl rand returned an empty encryption key" >&2
      return 1
    fi
    printf '%s' "$raw" | tr '/+' '_-' | tr -d '\r\n'
  else
    docker run --rm python:3.13-alpine python -c \
      "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
  fi
}

replace_env() {
  key="$1"
  value="$2"
  temp_file="${INSTALL_DIR}/.env.tmp"
  awk -v key="$key" -v value="$value" '
    index($0, key "=") == 1 { print key "=" value; next }
    { print }
  ' "$INSTALL_DIR/.env" > "$temp_file"
  mv "$temp_file" "$INSTALL_DIR/.env"
}

api_token="$(random_hex 32)"
bootstrap_token="$(random_hex 32)"
credential_encryption_key="$(random_fernet)"
if [ -z "$credential_encryption_key" ]; then
  echo "Failed to generate CREDENTIAL_ENCRYPTION_KEY" >&2
  exit 1
fi
replace_env API_AUTH_TOKEN "$api_token"
replace_env BOOTSTRAP_ADMIN_TOKEN "$bootstrap_token"
replace_env SECRET_KEY "$(random_hex 32)"
replace_env CREDENTIAL_ENCRYPTION_KEY "$credential_encryption_key"
chmod 600 "$INSTALL_DIR/.env"

cd "$INSTALL_DIR"
docker compose pull api frontend agent-1
docker compose up -d

attempt=0
until curl -fsS "http://localhost:${FRONTEND_PORT:-3010}/login" >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    docker compose ps
    docker compose logs --tail=100 api frontend
    echo "OpenCLI Admin did not become healthy within 5 minutes." >&2
    exit 1
  fi
  sleep 5
done

printf '\nOpenCLI Admin %s is ready.\n' "$VERSION"
printf 'URL: http://localhost:%s\n' "${FRONTEND_PORT:-3010}"
printf 'BOOTSTRAP_ADMIN_TOKEN: %s\n' "$bootstrap_token"
printf 'API_AUTH_TOKEN: %s\n' "$api_token"
printf 'Use BOOTSTRAP_ADMIN_TOKEN in the first login field and API_AUTH_TOKEN in the optional fleet field. Both are stored in %s/.env\n' "$INSTALL_DIR"
