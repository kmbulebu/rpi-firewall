# Development

Use Python 3.12 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/).
The repository is an Ansible project; it does not install a Python application.

From the repository root, install the locked development tools and the pinned
collections:

```sh
uv sync --locked
export ANSIBLE_COLLECTIONS_PATH="$PWD/.ansible/collections"
uv run --locked ansible-galaxy collection install --no-cache -r collections/requirements.yml
```

The virtual environment and collections stay in ignored local directories.
`collections/requirements.yml` is the single collection manifest used by local
development, CI, and the router's bootstrap playbook. Python dependencies are
locked in `uv.lock`; `uv sync --locked` rejects an out-of-date lockfile.

Run the same checks as the lint CI job:

```sh
uv run --locked python -m unittest discover -s tests -v
uv run --locked ansible-lint --profile=min playbook.yml
uv run --locked ansible-playbook -i rpi/inventory.yml playbook.yml --syntax-check
```

Before the first deployment, bootstrap dependencies **on the target**. The
bootstrap playbook requires Ubuntu's distribution-provided `ansible-core` and
an on-target repository checkout. It reads the collection manifest from that
checkout; running it over SSH from the development machine does not transfer
the manifest and is not supported.

For a target without a checkout, run the following in a shell **on that target**:

```sh
sudo apt-get update
sudo apt-get install --no-install-recommends ansible-core git
git clone https://github.com/kmbulebu/rpi-firewall.git rpi-firewall-bootstrap
cd rpi-firewall-bootstrap
sudo /usr/bin/ansible-playbook -i localhost, -c local bootstrap.yml
```

If a checkout already exists on the target, run the last command from its root
instead. The explicit local connection keeps bootstrap execution and the
manifest on the same host. Bootstrap needs apt and Ansible Galaxy access to
install target dependencies and collections. It does not install uv on the
router. The existing ansible-pull service already performs this on-target
bootstrap from its own checkout before applying the role; an operational router
using that service does not need a separate manual bootstrap.

Back on the **development machine**, use a private SSH inventory for your target
and the local tools/collections installed above. Review proposed changes with:

```sh
uv run --locked ansible-playbook -i /path/to/inventory.yml playbook.yml --check --diff
```

Check mode connects to the inventory's hosts and may require sudo. Diff output
can contain private configuration; keep it out of public issues and commits.
After reviewing the proposed changes, apply the role from the development machine:

```sh
uv run --locked ansible-playbook -i /path/to/inventory.yml playbook.yml
```

To intentionally update Python tooling, run `uv lock --upgrade`, review the
lockfile changes, and rerun the checks. Collection upgrades are separate edits to
`collections/requirements.yml`.

The `tests/integration_*.py` scripts require their documented disposable containers
and capabilities. They must not be run directly on a production router. See
[the forwarding lab](tests/README.forwarding.md), [DHCP packet tests](tests/README.dhcp.md),
and the GitHub Actions workflow
for the current integration commands.
