from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from onyx_desktop.acceptance_scope import BACKEND_MODES, validate_acceptance


class AcceptanceIsolationTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.fixture = self.root/'fixture'
        self.fixture.mkdir()
        (self.fixture/'.onyx-acceptance-root').write_text('isolated fixture')
        (self.fixture/'config.toml').write_text('[agent]\nname = "Onyx"\n')

    def tearDown(self):
        self.temp.cleanup()

    def args(self, mode, **extra):
        return SimpleNamespace(**dict({mode: str(self.root/'evidence'), 'backend_root': str(self.fixture), 'visual_test': False}, **extra))

    def test_every_backend_harness_requires_explicit_isolated_root(self):
        for mode in BACKEND_MODES:
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    validate_acceptance(self.args(mode, backend_root=None), self.fixture)
                with self.assertRaises(ValueError):
                    validate_acceptance(self.args(mode), self.root)
                with self.assertRaises(ValueError):
                    validate_acceptance(self.args(mode), self.fixture, self.fixture)

    def test_backend_harness_preferences_are_durable_inside_its_data_root(self):
        for mode in BACKEND_MODES:
            args = self.args(mode)
            result = validate_acceptance(args, self.fixture)
            self.assertTrue(result.is_relative_to(self.fixture/'data'))
            self.assertEqual(result, validate_acceptance(args, self.fixture))
            self.assertNotEqual(result, validate_acceptance(self.args(mode, **{mode: str(self.root/'other')}), self.fixture))
        self.assertFalse((self.fixture/'data').exists())  # validation itself never writes

    def test_normal_launch_is_untouched_and_modes_cannot_overlap(self):
        self.assertIsNone(validate_acceptance(SimpleNamespace(), self.root))
        with self.assertRaises(ValueError):
            validate_acceptance(self.args('acceptance_dir', workflow_acceptance_dir='elsewhere'), self.fixture)
        with self.assertRaises(ValueError):
            validate_acceptance(self.args('production_acceptance_dir', visual_test=True), self.fixture)

    def test_escaped_database_or_data_junction_is_rejected(self):
        original = Path.resolve
        for escaped in (self.fixture/'data', self.fixture/'data/kk.db', self.fixture/'config.toml'):
            def resolve(path, *args, **kwargs):
                return self.root/'production' if path == escaped else original(path, *args, **kwargs)
            with self.subTest(path=escaped), patch.object(Path, 'resolve', resolve):
                with self.assertRaises(ValueError):
                    validate_acceptance(self.args('acceptance_dir'), self.fixture)
