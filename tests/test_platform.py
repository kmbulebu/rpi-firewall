"""Check platform defaults and optional exporter affinity."""
from pathlib import Path
import unittest

from jinja2 import Environment, FileSystemLoader, StrictUndefined
import yaml

ROLE = Path(__file__).resolve().parents[1] / 'roles/firewall'


class PlatformTests(unittest.TestCase):
    def test_defaults_preserve_pi_setup_and_vm_exporter_has_no_cpu_pin(self):
        values = yaml.safe_load((ROLE / 'defaults/main.yml').read_text())
        self.assertIs(values['manage_fstab'], True)
        self.assertIs(values['rpi_tuning_enabled'], True)
        env = Environment(loader=FileSystemLoader(ROLE / 'templates'), undefined=StrictUndefined)
        env.filters['bool'] = bool
        template = env.get_template('node-exporter/10-priority.conf')
        self.assertIn('CPUAffinity=3\n', template.render(**values))
        values['rpi_tuning_enabled'] = False
        self.assertIn('CPUAffinity=\n', template.render(**values))
