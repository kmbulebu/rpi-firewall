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

Sync the local repo into the router VM and run the playbook:

```bash
scripts/testbench.sh sync
scripts/testbench.sh run
```

Verify DHCP/DNS from the client VM:

```bash
scripts/testbench.sh verify
```

Stop the VMs:

```bash
scripts/testbench.sh stop
```

Use `scripts/testbench.sh status` to see SSH connection details.
