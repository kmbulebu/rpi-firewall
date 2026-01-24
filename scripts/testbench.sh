#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
STATE_DIR="${ROOT_DIR}/scripts/state"
CLOUD_INIT_DIR="${ROOT_DIR}/scripts/cloud-init"
INVENTORY_TEMPLATE="${ROOT_DIR}/scripts/testbench-inventory.template.yml"
INVENTORY_PATH="${STATE_DIR}/testbench-inventory.yml"

ROUTER_NAME="router"
CLIENT_NAME="client"
ROUTER_USER="firewall"
CLIENT_USER="client"

ROUTER_SSH_PORT=${ROUTER_SSH_PORT:-2222}
CLIENT_SSH_PORT=${CLIENT_SSH_PORT:-2223}
LAN_SOCKET_PORT=${LAN_SOCKET_PORT:-12345}

ROUTER_WAN_MAC="52:54:00:aa:00:01"
ROUTER_LAN_MAC="52:54:00:aa:00:02"
CLIENT_MGMT_MAC="52:54:00:aa:00:03"
CLIENT_LAN_MAC="52:54:00:aa:00:04"

UBUNTU_RELEASE_PRIMARY="26.04"
UBUNTU_RELEASE_FALLBACK="24.04"
UBUNTU_ARCH="amd64"

ROUTER_DISK_SIZE=${ROUTER_DISK_SIZE:-"20G"}
CLIENT_DISK_SIZE=${CLIENT_DISK_SIZE:-"8G"}

SSH_KEY_PATH=${SSH_KEY_PATH:-"$HOME/.ssh/id_ed25519.pub"}
SSH_RETRY_COUNT=${SSH_RETRY_COUNT:-30}
SSH_RETRY_DELAY=${SSH_RETRY_DELAY:-5}

ensure_command() {
  local cmd=$1
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "Missing required command: $cmd" >&2
    exit 1
  fi
}

ensure_requirements() {
  ensure_command qemu-system-x86_64
  ensure_command qemu-img
  ensure_command curl
  ensure_command ssh
  ensure_command rsync
  if ! command -v cloud-localds >/dev/null 2>&1; then
    if ! command -v hdiutil >/dev/null 2>&1; then
      echo "Missing required command: cloud-localds or hdiutil" >&2
      exit 1
    fi
  fi
}

ensure_state_dir() {
  mkdir -p "$STATE_DIR"
}

sed_escape() {
  printf '%s' "$1" | sed -e 's/[\\&/]/\\&/g'
}

render_template() {
  local src=$1
  local dst=$2
  local ssh_key_escaped
  ssh_key_escaped=$(sed_escape "$SSH_PUB_KEY")
  local router_wan_escaped
  router_wan_escaped=$(sed_escape "$ROUTER_WAN_MAC")
  local router_lan_escaped
  router_lan_escaped=$(sed_escape "$ROUTER_LAN_MAC")
  local client_mgmt_escaped
  client_mgmt_escaped=$(sed_escape "$CLIENT_MGMT_MAC")
  local client_lan_escaped
  client_lan_escaped=$(sed_escape "$CLIENT_LAN_MAC")

  sed \
    -e "s/__SSH_PUB_KEY__/${ssh_key_escaped}/g" \
    -e "s/__ROUTER_WAN_MAC__/${router_wan_escaped}/g" \
    -e "s/__ROUTER_LAN_MAC__/${router_lan_escaped}/g" \
    -e "s/__CLIENT_MGMT_MAC__/${client_mgmt_escaped}/g" \
    -e "s/__CLIENT_LAN_MAC__/${client_lan_escaped}/g" \
    "$src" > "$dst"
}

wait_for_ssh() {
  local user=$1
  local port=$2
  local host=$3
  local attempt=1
  local ssh_opts
  ssh_opts="-p ${port} -o StrictHostKeyChecking=no -o UserKnownHostsFile=${STATE_DIR}/known_hosts -o ConnectTimeout=5"
  while (( attempt <= SSH_RETRY_COUNT )); do
    if ssh $ssh_opts "${user}@${host}" "echo ready" >/dev/null 2>&1; then
      return
    fi
    echo "Waiting for SSH on ${host}:${port} (attempt ${attempt}/${SSH_RETRY_COUNT})..." >&2
    sleep "$SSH_RETRY_DELAY"
    attempt=$((attempt + 1))
  done
  echo "SSH not available on ${host}:${port} after ${SSH_RETRY_COUNT} attempts." >&2
  exit 1
}

wait_for_remote_command() {
  local user=$1
  local port=$2
  local host=$3
  local command=$4
  local attempt=1
  local ssh_opts
  ssh_opts="-p ${port} -o StrictHostKeyChecking=no -o UserKnownHostsFile=${STATE_DIR}/known_hosts -o ConnectTimeout=5"
  while (( attempt <= SSH_RETRY_COUNT )); do
    if ssh $ssh_opts "${user}@${host}" "$command" >/dev/null 2>&1; then
      return
    fi
    echo "Waiting for '${command}' on ${host}:${port} (attempt ${attempt}/${SSH_RETRY_COUNT})..." >&2
    sleep "$SSH_RETRY_DELAY"
    attempt=$((attempt + 1))
  done
  echo "Command '${command}' not available on ${host}:${port} after ${SSH_RETRY_COUNT} attempts." >&2
  exit 1
}

resolve_ubuntu_release() {
  local primary_url
  primary_url="https://cloud-images.ubuntu.com/releases/${UBUNTU_RELEASE_PRIMARY}/release/ubuntu-${UBUNTU_RELEASE_PRIMARY}-server-cloudimg-${UBUNTU_ARCH}.img"
  if curl -fsI "$primary_url" >/dev/null 2>&1; then
    echo "$UBUNTU_RELEASE_PRIMARY"
    return
  fi
  echo "$UBUNTU_RELEASE_FALLBACK"
}

download_image() {
  local release=$1
  local image_name="ubuntu-${release}-server-cloudimg-${UBUNTU_ARCH}.img"
  local image_url="https://cloud-images.ubuntu.com/releases/${release}/release/${image_name}"
  local image_path="${STATE_DIR}/${image_name}"

  if [[ -f "$image_path" ]]; then
    echo "$image_path"
    return
  fi

  echo "Downloading Ubuntu ${release} cloud image..." >&2
  curl -fL "$image_url" -o "$image_path"
  echo "$image_path"
}

create_disk() {
  local base_image=$1
  local disk_path=$2
  local disk_size=$3
  if [[ ! -f "$disk_path" ]]; then
    qemu-img create -f qcow2 -F qcow2 -b "$base_image" "$disk_path" >/dev/null
  fi
  qemu-img resize "$disk_path" "$disk_size" >/dev/null
}

create_seed() {
  local user_data=$1
  local meta_data=$2
  local network_config=$3
  local seed_path=$4
  if [[ -f "$seed_path" ]]; then
    rm -f "$seed_path"
  fi
  if [[ -f "${seed_path}.iso" ]]; then
    rm -f "${seed_path}.iso"
  fi
  if command -v cloud-localds >/dev/null 2>&1; then
    cloud-localds --network-config="$network_config" "$seed_path" "$user_data" "$meta_data" >/dev/null
    return
  fi

  local tmp_dir
  tmp_dir=$(mktemp -d)
  cp "$user_data" "${tmp_dir}/user-data"
  cp "$meta_data" "${tmp_dir}/meta-data"
  cp "$network_config" "${tmp_dir}/network-config"
  hdiutil makehybrid -o "$seed_path" -iso -joliet -default-volume-name cidata "$tmp_dir" >/dev/null
  rm -rf "$tmp_dir"
}

write_inventory() {
  if [[ -z "${SSH_PUB_KEY:-}" ]]; then
    if [[ ! -f "$SSH_KEY_PATH" ]]; then
      echo "SSH public key not found at $SSH_KEY_PATH" >&2
      exit 1
    fi
    SSH_PUB_KEY=$(cat "$SSH_KEY_PATH")
  fi
  render_template "$INVENTORY_TEMPLATE" "$INVENTORY_PATH"
}

start_vms() {
  ensure_requirements
  ensure_state_dir

  if [[ ! -f "$SSH_KEY_PATH" ]]; then
    echo "SSH public key not found at $SSH_KEY_PATH" >&2
    exit 1
  fi
  SSH_PUB_KEY=$(cat "$SSH_KEY_PATH")

  local release
  release=$(resolve_ubuntu_release)
  if [[ "$release" != "$UBUNTU_RELEASE_PRIMARY" ]]; then
    echo "Ubuntu ${UBUNTU_RELEASE_PRIMARY} not available, using ${release}."
  fi

  local base_image
  base_image=$(download_image "$release")

  local router_disk="${STATE_DIR}/router.qcow2"
  local client_disk="${STATE_DIR}/client.qcow2"
  create_disk "$base_image" "$router_disk" "$ROUTER_DISK_SIZE"
  create_disk "$base_image" "$client_disk" "$CLIENT_DISK_SIZE"

  local router_user_data="${STATE_DIR}/router-user-data.yml"
  local router_meta_data="${STATE_DIR}/router-meta-data.yml"
  local router_network_config="${STATE_DIR}/router-network-config.yml"
  local client_user_data="${STATE_DIR}/client-user-data.yml"
  local client_meta_data="${STATE_DIR}/client-meta-data.yml"
  local client_network_config="${STATE_DIR}/client-network-config.yml"

  render_template "${CLOUD_INIT_DIR}/router-user-data.yml" "$router_user_data"
  render_template "${CLOUD_INIT_DIR}/router-meta-data.yml" "$router_meta_data"
  render_template "${CLOUD_INIT_DIR}/router-network-config.yml" "$router_network_config"
  render_template "${CLOUD_INIT_DIR}/client-user-data.yml" "$client_user_data"
  render_template "${CLOUD_INIT_DIR}/client-meta-data.yml" "$client_meta_data"
  render_template "${CLOUD_INIT_DIR}/client-network-config.yml" "$client_network_config"

  local router_seed="${STATE_DIR}/router-seed.iso"
  local client_seed="${STATE_DIR}/client-seed.iso"
  create_seed "$router_user_data" "$router_meta_data" "$router_network_config" "$router_seed"
  create_seed "$client_user_data" "$client_meta_data" "$client_network_config" "$client_seed"

  write_inventory

  qemu-system-x86_64 \
    -machine accel=hvf \
    -cpu host \
    -smp 2 \
    -m 2048 \
    -drive file="$router_disk",if=virtio,format=qcow2 \
    -drive file="$router_seed",format=raw,media=cdrom \
    -netdev user,id=wan,hostfwd=tcp::${ROUTER_SSH_PORT}-:22 \
    -device virtio-net-pci,netdev=wan,mac=${ROUTER_WAN_MAC} \
    -netdev socket,id=lan,listen=:${LAN_SOCKET_PORT} \
    -device virtio-net-pci,netdev=lan,mac=${ROUTER_LAN_MAC} \
    -display none \
    -serial none \
    -monitor none \
    -pidfile "${STATE_DIR}/router.pid" \
    -daemonize

  qemu-system-x86_64 \
    -machine accel=hvf \
    -cpu host \
    -smp 2 \
    -m 1024 \
    -drive file="$client_disk",if=virtio,format=qcow2 \
    -drive file="$client_seed",format=raw,media=cdrom \
    -netdev user,id=mgmt,hostfwd=tcp::${CLIENT_SSH_PORT}-:22 \
    -device virtio-net-pci,netdev=mgmt,mac=${CLIENT_MGMT_MAC} \
    -netdev socket,id=lan,connect=127.0.0.1:${LAN_SOCKET_PORT} \
    -device virtio-net-pci,netdev=lan,mac=${CLIENT_LAN_MAC} \
    -display none \
    -serial none \
    -monitor none \
    -pidfile "${STATE_DIR}/client.pid" \
    -daemonize

  echo "Router SSH: ssh -p ${ROUTER_SSH_PORT} ${ROUTER_USER}@127.0.0.1"
  echo "Client SSH: ssh -p ${CLIENT_SSH_PORT} ${CLIENT_USER}@127.0.0.1"
}

sync_repo() {
  ensure_state_dir
  write_inventory
  if [[ ! -f "$INVENTORY_PATH" ]]; then
    echo "Testbench inventory not found. Run 'start' first." >&2
    exit 1
  fi

  local ssh_opts
  ssh_opts="-p ${ROUTER_SSH_PORT} -o StrictHostKeyChecking=no -o UserKnownHostsFile=${STATE_DIR}/known_hosts"
  wait_for_ssh "$ROUTER_USER" "$ROUTER_SSH_PORT" "127.0.0.1"
  ssh $ssh_opts "${ROUTER_USER}@127.0.0.1" "mkdir -p /home/${ROUTER_USER}/rpi-firewall"
  rsync -az --delete \
    --exclude ".git" \
    --exclude "scripts/state" \
    -e "ssh ${ssh_opts}" \
    "${ROOT_DIR}/" "${ROUTER_USER}@127.0.0.1:/home/${ROUTER_USER}/rpi-firewall/"
  ssh $ssh_opts "${ROUTER_USER}@127.0.0.1" "mkdir -p /home/${ROUTER_USER}/rpi-firewall/scripts/state"
  scp -P "${ROUTER_SSH_PORT}" -o StrictHostKeyChecking=no -o UserKnownHostsFile=${STATE_DIR}/known_hosts \
    "$INVENTORY_PATH" "${ROUTER_USER}@127.0.0.1:/home/${ROUTER_USER}/rpi-firewall/scripts/state/testbench-inventory.yml"
}

run_playbook() {
  ensure_state_dir
  local ssh_opts
  ssh_opts="-p ${ROUTER_SSH_PORT} -o StrictHostKeyChecking=no -o UserKnownHostsFile=${STATE_DIR}/known_hosts"
  wait_for_ssh "$ROUTER_USER" "$ROUTER_SSH_PORT" "127.0.0.1"
  if ssh $ssh_opts "${ROUTER_USER}@127.0.0.1" "command -v cloud-init" >/dev/null 2>&1; then
    ssh $ssh_opts "${ROUTER_USER}@127.0.0.1" "cloud-init status --wait" >/dev/null
  fi
  wait_for_remote_command "$ROUTER_USER" "$ROUTER_SSH_PORT" "127.0.0.1" "command -v ansible-playbook"
  ssh $ssh_opts "${ROUTER_USER}@127.0.0.1" \
    "cd /home/${ROUTER_USER}/rpi-firewall && ansible-playbook -i scripts/state/testbench-inventory.yml playbook.yml"
}

verify_client() {
  ensure_state_dir
  local ssh_opts
  ssh_opts="-p ${CLIENT_SSH_PORT} -o StrictHostKeyChecking=no -o UserKnownHostsFile=${STATE_DIR}/known_hosts"
  wait_for_ssh "$CLIENT_USER" "$CLIENT_SSH_PORT" "127.0.0.1"
  ssh $ssh_opts "${CLIENT_USER}@127.0.0.1" "ip -4 addr show lan0"
  ssh $ssh_opts "${CLIENT_USER}@127.0.0.1" "resolvectl status lan0 || true"
  ssh $ssh_opts "${CLIENT_USER}@127.0.0.1" "dig +short example.com"
  ssh $ssh_opts "${CLIENT_USER}@127.0.0.1" "ping -c 1 1.1.1.1"
}

stop_vms() {
  local pid_file
  for pid_file in "${STATE_DIR}/router.pid" "${STATE_DIR}/client.pid"; do
    if [[ -f "$pid_file" ]]; then
      local pid
      pid=$(cat "$pid_file")
      if kill -0 "$pid" >/dev/null 2>&1; then
        kill "$pid" || true
      fi
      rm -f "$pid_file"
    fi
  done
}

status_vms() {
  local pid_file
  for pid_file in "${STATE_DIR}/router.pid" "${STATE_DIR}/client.pid"; do
    if [[ -f "$pid_file" ]]; then
      local pid
      pid=$(cat "$pid_file")
      if kill -0 "$pid" >/dev/null 2>&1; then
        echo "${pid_file##*/}: running (pid ${pid})"
      else
        echo "${pid_file##*/}: stale"
      fi
    else
      echo "${pid_file##*/}: not running"
    fi
  done
  echo "Router SSH: ssh -p ${ROUTER_SSH_PORT} ${ROUTER_USER}@127.0.0.1"
  echo "Client SSH: ssh -p ${CLIENT_SSH_PORT} ${CLIENT_USER}@127.0.0.1"
}

usage() {
  cat <<'EOF'
Usage: scripts/testbench.sh <command>

Commands:
  start   Download images, create disks, and launch VMs
  sync    Sync local repo into the router VM
  run     Run ansible-playbook inside the router VM
  verify  Verify DHCP/DNS/routing from the client VM
  stop    Stop running VMs
  status  Show VM status and SSH commands

Environment overrides:
  ROUTER_SSH_PORT, CLIENT_SSH_PORT, LAN_SOCKET_PORT, SSH_KEY_PATH
EOF
}

main() {
  local cmd=${1:-}
  case "$cmd" in
    start)
      start_vms
      ;;
    sync)
      sync_repo
      ;;
    run)
      run_playbook
      ;;
    verify)
      verify_client
      ;;
    stop)
      stop_vms
      ;;
    status)
      status_vms
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
