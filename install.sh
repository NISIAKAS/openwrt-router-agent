#!/bin/sh
set -eu

API_URL="${NISIA_API_URL:-https://kasplex.store}"
INSTALL_TOKEN="${NISIA_INSTALL_TOKEN:-public}"
INTERVAL="${NISIA_INTERVAL:-30}"
TLS_INSECURE="${NISIA_TLS_INSECURE:-0}"

log() {
	printf '%s\n' "nisia-agent-install: $*"
}

fetch() {
	url="$1"
	dst="$2"
	if command -v wget >/dev/null 2>&1; then
		wget -qO "$dst" "$url"
	elif command -v curl >/dev/null 2>&1; then
		curl -fsSL "$url" -o "$dst"
	else
		echo "Neither wget nor curl is available" >&2
		return 1
	fi
}

install_deps() {
	missing=""
	command -v curl >/dev/null 2>&1 || missing="$missing curl"
	command -v jq >/dev/null 2>&1 || missing="$missing jq"
	[ -z "$missing" ] && return 0

	log "Installing missing packages:$missing"
	if command -v apk >/dev/null 2>&1; then
		apk update
		apk add $missing
	elif command -v opkg >/dev/null 2>&1; then
		opkg update
		opkg install $missing
	else
		echo "No supported package manager found for:$missing" >&2
		return 1
	fi
}

install_deps

tmp_dir="/tmp/nisia-agent-install.$$"
mkdir -p "$tmp_dir"
trap 'rm -rf "$tmp_dir"' EXIT

fetch "$API_URL/download/nisia-agent" "$tmp_dir/nisia-agent"
fetch "$API_URL/download/nisia-agent-init" "$tmp_dir/nisia-agent-init"

install -m 0755 "$tmp_dir/nisia-agent" /usr/bin/nisia-agent
install -m 0755 "$tmp_dir/nisia-agent-init" /etc/init.d/nisia-agent

uci -q batch <<EOF
set nisia_agent.main=agent
set nisia_agent.main.enabled='1'
set nisia_agent.main.api_url='$API_URL'
set nisia_agent.main.tls_insecure='$TLS_INSECURE'
set nisia_agent.main.install_token='$INSTALL_TOKEN'
set nisia_agent.main.interval='$INTERVAL'
delete nisia_agent.main.device_id
delete nisia_agent.main.device_token
commit nisia_agent
EOF

/etc/init.d/nisia-agent enable
/etc/init.d/nisia-agent restart
/usr/bin/nisia-agent run_once || true

device_id="$(uci -q get nisia_agent.main.device_id 2>/dev/null || true)"
log "installed. device_id=${device_id:-pending-registration}"
