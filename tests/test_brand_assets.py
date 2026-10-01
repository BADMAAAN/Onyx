"""Validate the distributed bytes and actual Qt decoding of pinned brand assets."""
import hashlib
import json
from pathlib import Path
from unittest import TestCase

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


class BrandAssetTests(TestCase):
    def test_manifest_fingerprints_match_every_distributed_asset(self):
        folder = Path(__file__).resolve().parents[1] / 'onyx_desktop' / 'assets'
        for entry in json.loads((folder / 'app-sources.json').read_text(encoding='utf-8')):
            with self.subTest(asset=entry['name']):
                asset = folder / entry.get('filename', entry['name'] + '.png')
                self.assertEqual(hashlib.sha256(asset.read_bytes()).hexdigest(), entry['sha256'])
                if entry.get('original_filename'):
                    original = folder / entry['original_filename']
                    self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), entry['source_sha256'])
                if entry.get('notice'):
                    self.assertTrue((folder / entry['notice']).is_file())

    def test_pinned_images_decode_with_the_shipped_qt_formats(self):
        folder = Path(__file__).resolve().parents[1] / 'onyx_desktop' / 'assets'
        for entry in json.loads((folder / 'app-sources.json').read_text(encoding='utf-8')):
            with self.subTest(asset=entry['name']):
                asset = folder / entry.get('filename', entry['name'] + '.png')
                if asset.suffix == '.svg':
                    renderer = QSvgRenderer(str(asset))
                    self.assertTrue(renderer.isValid())
                    image = QImage(48, 48, QImage.Format_ARGB32)
                    image.fill(Qt.transparent)
                    painter = QPainter(image)
                    renderer.render(painter)
                    painter.end()
                    pixels = [image.pixelColor(x, y) for x in range(48) for y in range(48)]
                    self.assertTrue(any(pixel.alpha() > 0 for pixel in pixels), 'Valid SVG must actually render a visible mark')
                    if entry['name'] == 'factory':
                        self.assertTrue(any(pixel.alpha() > 0 and pixel.lightness() > 128 for pixel in pixels), 'Factory mark must remain visible against the dark card')
                else:
                    image = QImage(str(asset))
                    self.assertFalse(image.isNull())
                    self.assertGreater(image.width(), 0)
                    self.assertGreater(image.height(), 0)
