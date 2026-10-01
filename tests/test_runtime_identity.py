from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from kk.core.runtime_identity import source_manifest


class RuntimeIdentityTests(unittest.TestCase):
    def test_fingerprint_tracks_renderer_and_source_but_not_runtime_memory(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'app').mkdir()
            (root / 'kk/ui').mkdir(parents=True)
            (root / 'data').mkdir()
            (root / 'app/main.py').write_text('a = 1')
            shader = root / 'kk/ui/edge.frag.qsb'
            shader.write_bytes(b'original shader')
            first = source_manifest(root)
            (root / 'data/kk.db').write_bytes(b'private runtime state')
            self.assertEqual(first, source_manifest(root))
            shader.write_bytes(b'new shader')
            second = source_manifest(root)
            self.assertNotEqual(first['source_sha256'], second['source_sha256'])
            self.assertEqual(first['source_files']['app/main.py'], second['source_files']['app/main.py'])
            self.assertNotIn('data/kk.db', second['source_files'])
