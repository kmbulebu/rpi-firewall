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

Use a private inventory for your target. Review proposed changes with:

```sh
uv run --locked ansible-playbook -i /path/to/inventory.yml playbook.yml --check --diff
```

Check mode connects to the inventory's hosts and may require sudo. Diff output
can contain private configuration; keep it out of public issues and commits.
For a real deployment, bootstrap target dependencies before applying the role:

```sh
uv run --locked ansible-playbook -i /path/to/inventory.yml bootstrap.yml
uv run --locked ansible-playbook -i /path/to/inventory.yml playbook.yml
```

Bootstrap installs target-side dependencies and collections; it does not install
the local uv environment on the router. The existing ansible-pull deployment path
continues to use the distribution's Ansible installation.

To intentionally update Python tooling, run `uv lock --upgrade`, review the
lockfile changes, and rerun the checks. Collection upgrades are separate edits to
`collections/requirements.yml`.

The `tests/integration_*.py` scripts require their documented disposable containers
and capabilities. They must not be run directly on a production router. See
[the forwarding lab](tests/README.forwarding.md) and the GitHub Actions workflow
for the current integration commands.
