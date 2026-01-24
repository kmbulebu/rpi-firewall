# AGENTS.md

## Purpose
This repository is an Ansible playbook that configures a Raspberry Pi router/firewall.
Agents should prioritize safe infrastructure changes and keep playbook behavior
explicit and auditable.

## Design Principles
- Prefer systemd-native capabilities (systemd-networkd, systemd-resolved, udev).
- Avoid adding new software unless absolutely required; the current exception is
  `unbound` for DNS features systemd-resolved lacks.
- Do not switch to other technologies (for example, dnsmasq) just to satisfy a
  requested feature. Push back and offer systemd-based alternatives instead.
- Use these references when making network changes:
  - https://manpages.ubuntu.com/manpages/noble/man5/systemd.network.5.html
  - https://manpages.ubuntu.com/manpages/noble/en/man5/systemd.netdev.5.html
  - https://manpages.ubuntu.com/manpages/noble/man5/systemd.link.5.html
  - https://manpages.ubuntu.com/manpages/noble/man5/resolved.conf.5.html

## Repository Layout
- `playbook.yml`: top-level playbook.
- `roles/firewall/tasks/*.yml`: main role task files, ordered in `roles/firewall/tasks/main.yml`.
- `roles/firewall/templates/**`: Jinja templates for systemd-networkd, firewalld,
  unbound, and other services.
- `roles/firewall/files/tools/show_dhcp_leases.py`: helper tool installed on target.
- `rpi/`: cloud-init artifacts for first boot.

## Commands (Build / Lint / Test)
There is no traditional build step. The core checks are Ansible linting and
playbook dry-runs.

### Tooling (uv)
- Install Ansible tooling:
  - `uv sync`
- Run lint via uv:
  - `uv run ansible-lint playbook.yml`
- Run playbook via uv:
  - `uv run ansible-playbook -i rpi/inventory.yml playbook.yml --check --diff`

### Lint
- Full lint:
  - `ansible-lint playbook.yml`
- Lint a single file (closest to a single-test run):
  - `ansible-lint roles/firewall/tasks/network.yml`
  - `ansible-lint roles/firewall/templates/firewalld/firewalld.conf`

### Run / Check (playbook)
- Run against an inventory:
  - `ansible-playbook -i rpi/inventory.yml playbook.yml`
- Dry-run with diff (safe check):
  - `ansible-playbook -i rpi/inventory.yml playbook.yml --check --diff`

### Task-scoped runs (closest to single test)
Only some tasks are tagged. For example:
- `ansible-playbook -i rpi/inventory.yml playbook.yml --tags tools`

## Code Style Guidelines
Follow existing Ansible and Python conventions used in the repo.

### Ansible YAML
- Use YAML documents starting with `---` for tasks/handlers.
- Task names are imperative and capitalized (e.g., "Configure lan network").
- Prefer `name:` for every task; keep it short and descriptive.
- Use consistent indentation (2 spaces) and avoid tabs.
- Use explicit modules (`template`, `copy`, `service`, `apt`, `sysctl`, `modprobe`).
- Prefer `state: present/absent/started` instead of implicit defaults.
- Use `notify` with handlers for service restarts; avoid ad-hoc `command` restarts
  unless necessary.
- Use `become: yes` at play level when needed; avoid repeating per task.
- Booleans use `yes`/`no` in YAML (existing convention).
- Variables are `snake_case` and defined in `roles/firewall/defaults/main.yml` or
  in inventory.

### Jinja Templates
- Use `{{ variable }}` with spaces.
- Keep one setting per line in config templates.
- Jinja control blocks use `{% for ... %}` and `{% endfor %}` with blank lines
  where readability helps, as seen in `templates/unbound/server.conf`.
- Prefer `default([], true)` in loops when input may be undefined.

### Python (tools)
- Follows PEP 8-ish style with explicit imports and standard library first.
- Use small helper functions and docstrings for non-trivial logic.
- Error handling is explicit: raise `RuntimeError` with context or print to
  `stderr` and exit with non-zero codes.
- Avoid external dependencies unless installed via Ansible (e.g., dnspython is
  installed in `roles/firewall/tasks/tools.yml`).

### Naming Conventions
- Variables: `snake_case` (e.g., `lan_iface`, `wan_iface_networkd_link_match`).
- Templates and files: kebab-case or snake_case as already used.
- Handlers: imperative verbs (e.g., "restart systemd-networkd").

### Error Handling & Safety
- Prefer idempotent Ansible modules over `command`/`shell`.
- If a command task is required, ensure `creates`/`removes` or clear guard
  conditions are used.
- Do not remove packages or change firewall defaults without explicit intent.
- Be cautious with networking changes; ensure `notify` handlers are set to reload
  services appropriately.

## Inventory / Secrets
- Inventory values are expected in `rpi/inventory.yml` or host vars.
- Do not commit secrets or private keys; reference variables and document them
  in README instead.

## Cursor / Copilot Rules
- No `.cursor/rules/` or `.cursorrules` files are present.
- No `.github/copilot-instructions.md` file is present.

## Agent Workflow Tips
- Read `roles/firewall/tasks/main.yml` to understand task ordering.
- Update templates and defaults together when adding a new variable.
- When adding a new handler, register it in `roles/firewall/handlers/main.yml`.
- Keep playbook changes compatible with Ubuntu Server 24.04.
- When adding tools, ensure installation tasks are tagged consistently.

## Common Locations
- Defaults: `roles/firewall/defaults/main.yml`
- Handlers: `roles/firewall/handlers/main.yml`
- Templates: `roles/firewall/templates/`
- DHCP leases helper: `roles/firewall/files/tools/show_dhcp_leases.py`

## Notes on Testing Strategy
- Use `ansible-lint` for style and rule compliance.
- Use `ansible-playbook --check --diff` for verification runs.
- There are no automated unit tests in this repo currently.
