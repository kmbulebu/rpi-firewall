## Development notes

This repo ships an optional local testbench for macOS/Linux using QEMU and cloud-init. It is intended to validate Ansible changes without a physical Pi.

## Tooling (uv)

```bash
uv sync
uv run ansible-lint playbook.yml
uv run ansible-playbook -i rpi/inventory.yml playbook.yml --check --diff
```

Install required collections (if needed):

```bash
uv run ansible-galaxy collection install -r requirements.yml
```

## QEMU testbench

Start the router and client VMs:

```bash
scripts/testbench.sh start
```

To use the daily Resolute (26.04) image instead of the stable channel:

```bash
UBUNTU_IMAGE_CHANNEL=daily scripts/testbench.sh start
```

The daily channel fails fast if the image is unavailable.

Sync the local repo into the router VM and run the playbook:

```bash
scripts/testbench.sh sync
scripts/testbench.sh run
```

`scripts/testbench.sh run` executes `bootstrap.yml` before `playbook.yml` to
install pinned Ansible collections.

Verify DHCP/DNS from the client VM:

```bash
scripts/testbench.sh verify
```

Stop the VMs:

```bash
scripts/testbench.sh stop
```

Use `scripts/testbench.sh status` to see QGA/serial connection details.

The testbench uses a static resolver during cloud-init (via bootcmd) to avoid
early DNS failures while packages install.

The testbench uses QEMU guest agent (QGA) sockets for sync/run/verify, so SSH is
not required for automation.

QGA sockets:

```bash
scripts/state/router-qga.sock
scripts/state/client-qga.sock
```

Sync includes local uncommitted changes (the tarball excludes `.git`).

### Testbench console access

If SSH is unavailable, use the serial console sockets:

```bash
nc -U scripts/state/router-serial.sock
nc -U scripts/state/client-serial.sock
```

Console login credentials (testbench only):

- Router user: `firewall`
- Client user: `client`
- Password: `MyVoiceIsMyPassword` (hash in cloud-init)
