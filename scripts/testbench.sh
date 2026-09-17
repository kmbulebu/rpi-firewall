#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
STATE_DIR="${ROOT_DIR}/scripts/state"
CLOUD_INIT_DIR="${ROOT_DIR}/scripts/cloud-init"
INVENTORY_TEMPLATE="${ROOT_DIR}/scripts/testbench-inventory.template.yml"
INVENTORY_PATH="${STATE_DIR}/testbench-inventory.yml"
QGA_HELPER="${ROOT_DIR}/scripts/qga.py"

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
UBUNTU_IMAGE_CHANNEL=${UBUNTU_IMAGE_CHANNEL:-"release"}
IMAGE_CACHE_DIR=${IMAGE_CACHE_DIR:-""}

ROUTER_DISK_SIZE=${ROUTER_DISK_SIZE:-"20G"}
CLIENT_DISK_SIZE=${CLIENT_DISK_SIZE:-"8G"}

SSH_KEY_PATH=${SSH_KEY_PATH:-"$HOME/.ssh/id_ed25519.pub"}
SSH_RETRY_COUNT=${SSH_RETRY_COUNT:-30}
SSH_RETRY_DELAY=${SSH_RETRY_DELAY:-5}
QGA_RETRY_COUNT=${QGA_RETRY_COUNT:-$SSH_RETRY_COUNT}
QGA_RETRY_DELAY=${QGA_RETRY_DELAY:-$SSH_RETRY_DELAY}

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
  ensure_command python3
  ensure_command tar
  if [[ "$(uname -s)" == "Darwin" ]]; then
    QEMU_ACCEL_ARGS=("-machine" "accel=hvf")
  else
    QEMU_ACCEL_ARGS=("-enable-kvm" "-machine" "accel=kvm")
  fi
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

qga_exec() {
  local socket=$1
  shift
  python3 "$QGA_HELPER" --socket "$socket" exec "$@"
}

qga_exec_timeout() {
  local socket=$1
  local timeout=$2
  shift 2
  python3 "$QGA_HELPER" --socket "$socket" exec --timeout "$timeout" "$@"
}

qga_push() {
  local socket=$1
  local src=$2
  local dest=$3
  python3 "$QGA_HELPER" --socket "$socket" push --src "$src" --dest "$dest"
}

wait_for_qga() {
  local name=$1
  local socket=$2
  local attempt=1
  while (( attempt <= QGA_RETRY_COUNT )); do
    if python3 "$QGA_HELPER" --socket "$socket" exec "true" >/dev/null 2>&1; then
      return
    fi
    echo "Waiting for QGA on ${name} (attempt ${attempt}/${QGA_RETRY_COUNT})..." >&2
    sleep "$QGA_RETRY_DELAY"
    attempt=$((attempt + 1))
  done
  echo "QGA not available on ${name} after ${QGA_RETRY_COUNT} attempts." >&2
  exit 1
}

resolve_ubuntu_release() {
  if [[ "$UBUNTU_IMAGE_CHANNEL" == "daily" ]]; then
    echo "resolute"
    return
  fi

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
  local image_name
  local image_url
  if [[ "$UBUNTU_IMAGE_CHANNEL" == "daily" ]]; then
    image_name="${release}-server-cloudimg-${UBUNTU_ARCH}.img"
    image_url="https://cloud-images.ubuntu.com/${release}/current/${image_name}"
  else
    image_name="ubuntu-${release}-server-cloudimg-${UBUNTU_ARCH}.img"
    image_url="https://cloud-images.ubuntu.com/releases/${release}/release/${image_name}"
  fi
  local image_path="${STATE_DIR}/${image_name}"
  if [[ -n "$IMAGE_CACHE_DIR" ]]; then
    mkdir -p "$IMAGE_CACHE_DIR"
    image_path="${IMAGE_CACHE_DIR}/${image_name}"
  fi

  if [[ -f "$image_path" ]]; then
    echo "$image_path"
    return
  fi

  if [[ "$UBUNTU_IMAGE_CHANNEL" == "daily" ]]; then
    echo "Downloading Ubuntu daily ${release} cloud image..." >&2
    if ! curl -fsI "$image_url" >/dev/null 2>&1; then
      echo "Daily image not available: ${image_url}" >&2
      exit 1
    fi
  else
    echo "Downloading Ubuntu ${release} cloud image..." >&2
  fi
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
  if [[ "$UBUNTU_IMAGE_CHANNEL" == "daily" ]]; then
    echo "Using Ubuntu daily ${release} cloud image."
  elif [[ "$release" != "$UBUNTU_RELEASE_PRIMARY" ]]; then
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
  local router_serial_socket="${STATE_DIR}/router-serial.sock"
  local client_serial_socket="${STATE_DIR}/client-serial.sock"
  local router_monitor_socket="${STATE_DIR}/router-monitor.sock"
  local client_monitor_socket="${STATE_DIR}/client-monitor.sock"
  local router_qga_socket="${STATE_DIR}/router-qga.sock"
  local client_qga_socket="${STATE_DIR}/client-qga.sock"
  create_seed "$router_user_data" "$router_meta_data" "$router_network_config" "$router_seed"
  create_seed "$client_user_data" "$client_meta_data" "$client_network_config" "$client_seed"

  rm -f "$router_serial_socket" "$client_serial_socket" "$router_monitor_socket" "$client_monitor_socket" \
    "$router_qga_socket" "$client_qga_socket"

  write_inventory

  qemu-system-x86_64 \
    "${QEMU_ACCEL_ARGS[@]}" \
    -cpu host \
    -smp 2 \
    -m 2048 \
    -drive file="$router_disk",if=virtio,format=qcow2 \
    -drive file="$router_seed",format=raw,media=cdrom \
    -netdev user,id=wan,hostfwd=tcp::${ROUTER_SSH_PORT}-:22 \
    -device virtio-net-pci,netdev=wan,mac=${ROUTER_WAN_MAC} \
    -netdev socket,id=lan,listen=:${LAN_SOCKET_PORT} \
    -device virtio-net-pci,netdev=lan,mac=${ROUTER_LAN_MAC} \
    -device virtio-serial \
    -chardev socket,id=router_qga,path=${router_qga_socket},server=on,wait=off \
    -device virtserialport,chardev=router_qga,name=org.qemu.guest_agent.0 \
    -display none \
    -serial unix:${router_serial_socket},server,nowait \
    -monitor unix:${router_monitor_socket},server,nowait \
    -pidfile "${STATE_DIR}/router.pid" \
    -daemonize

  qemu-system-x86_64 \
    "${QEMU_ACCEL_ARGS[@]}" \
    -cpu host \
    -smp 2 \
    -m 1024 \
    -drive file="$client_disk",if=virtio,format=qcow2 \
    -drive file="$client_seed",format=raw,media=cdrom \
    -netdev user,id=mgmt,hostfwd=tcp::${CLIENT_SSH_PORT}-:22 \
    -device virtio-net-pci,netdev=mgmt,mac=${CLIENT_MGMT_MAC} \
    -netdev socket,id=lan,connect=127.0.0.1:${LAN_SOCKET_PORT} \
    -device virtio-net-pci,netdev=lan,mac=${CLIENT_LAN_MAC} \
    -device virtio-serial \
    -chardev socket,id=client_qga,path=${client_qga_socket},server=on,wait=off \
    -device virtserialport,chardev=client_qga,name=org.qemu.guest_agent.0 \
    -display none \
    -serial unix:${client_serial_socket},server,nowait \
    -monitor unix:${client_monitor_socket},server,nowait \
    -pidfile "${STATE_DIR}/client.pid" \
    -daemonize

  echo "Router QGA: ${router_qga_socket}"
  echo "Client QGA: ${client_qga_socket}"
  echo "Router serial (backup): nc -U ${router_serial_socket}"
  echo "Client serial (backup): nc -U ${client_serial_socket}"
  echo "Router monitor (backup): nc -U ${router_monitor_socket}"
  echo "Client monitor (backup): nc -U ${client_monitor_socket}"
}

sync_repo() {
  ensure_state_dir
  write_inventory
  if [[ ! -f "$INVENTORY_PATH" ]]; then
    echo "Testbench inventory not found. Run 'start' first." >&2
    exit 1
  fi

  local router_qga_socket="${STATE_DIR}/router-qga.sock"
  local archive_path="${STATE_DIR}/rpi-firewall.tar.gz"

  wait_for_qga "router" "$router_qga_socket"
  COPYFILE_DISABLE=1 tar --no-xattrs --no-acls --exclude='.git' --exclude='scripts/state' -czf "$archive_path" -C "$ROOT_DIR" .
  qga_exec "$router_qga_socket" "mkdir -p /home/${ROUTER_USER}/rpi-firewall"
  qga_push "$router_qga_socket" "$archive_path" "/tmp/rpi-firewall.tar.gz"
  qga_exec "$router_qga_socket" "tar -xzf /tmp/rpi-firewall.tar.gz -C /home/${ROUTER_USER}/rpi-firewall"
  qga_exec "$router_qga_socket" "mkdir -p /home/${ROUTER_USER}/rpi-firewall/scripts/state"
  qga_push "$router_qga_socket" "$INVENTORY_PATH" "/home/${ROUTER_USER}/rpi-firewall/scripts/state/testbench-inventory.yml"
  qga_exec "$router_qga_socket" "chown -R ${ROUTER_USER}:${ROUTER_USER} /home/${ROUTER_USER}/rpi-firewall"
  qga_exec "$router_qga_socket" "rm -f /tmp/rpi-firewall.tar.gz"
}

run_playbook() {
  ensure_state_dir
  local router_qga_socket="${STATE_DIR}/router-qga.sock"
  wait_for_qga "router" "$router_qga_socket"
  qga_exec "$router_qga_socket" "command -v cloud-init >/dev/null 2>&1 && cloud-init status --wait || true"
  qga_exec "$router_qga_socket" "command -v ansible-playbook"
  qga_exec_timeout "$router_qga_socket" 600 "cd /home/${ROUTER_USER}/rpi-firewall && sudo -u ${ROUTER_USER} ansible-playbook -i scripts/state/testbench-inventory.yml bootstrap.yml"
  qga_exec_timeout "$router_qga_socket" 1800 "cd /home/${ROUTER_USER}/rpi-firewall && sudo -u ${ROUTER_USER} ansible-playbook -i scripts/state/testbench-inventory.yml playbook.yml"
}

verify_client() {
  ensure_state_dir
  local client_qga_socket="${STATE_DIR}/client-qga.sock"
  wait_for_qga "client" "$client_qga_socket"
  qga_exec "$client_qga_socket" "ip -4 addr show lan0"
  qga_exec "$client_qga_socket" "resolvectl status lan0 || true"
  qga_exec "$client_qga_socket" "dig +short example.com"
  qga_exec "$client_qga_socket" "tracepath -n 1.1.1.1 || true"
  qga_exec "$client_qga_socket" "ping -c 1 1.1.1.1"
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
  echo "Router QGA: ${STATE_DIR}/router-qga.sock"
  echo "Client QGA: ${STATE_DIR}/client-qga.sock"
  echo "Router serial (backup): nc -U ${STATE_DIR}/router-serial.sock"
  echo "Client serial (backup): nc -U ${STATE_DIR}/client-serial.sock"
  echo "Router monitor (backup): nc -U ${STATE_DIR}/router-monitor.sock"
  echo "Client monitor (backup): nc -U ${STATE_DIR}/client-monitor.sock"
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
  status  Show VM status and QGA commands

Backup console access:
  Router serial:  nc -U scripts/state/router-serial.sock
  Client serial:  nc -U scripts/state/client-serial.sock
  Router monitor: nc -U scripts/state/router-monitor.sock
  Client monitor: nc -U scripts/state/client-monitor.sock

QGA sockets:
  Router QGA: scripts/state/router-qga.sock
  Client QGA: scripts/state/client-qga.sock

Environment overrides:
  ROUTER_SSH_PORT, CLIENT_SSH_PORT, LAN_SOCKET_PORT, SSH_KEY_PATH
  QGA_RETRY_COUNT, QGA_RETRY_DELAY
  UBUNTU_IMAGE_CHANNEL
  IMAGE_CACHE_DIR
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
