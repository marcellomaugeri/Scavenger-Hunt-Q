"""Checks for the App Lab import bundle; no downloads or board access."""
import hashlib
import io
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]


class PrepareTests(unittest.TestCase):
    def load(self):
        script = ROOT / 'scripts/prepare.py'
        self.assertTrue(script.exists(), 'Preparation script is missing')
        return runpy.run_path(str(script))

    def test_bundle_contains_app_and_model_without_local_files(self):
        prepare = self.load()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / 'app'
            for name in ('app.yaml', 'python/main.py', 'assets/logo.svg', 'sketch/sketch.ino',
                         'python/.env', 'python/__pycache__/main.pyc', 'python/models/old/file'):
                file = app / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('fixture')
            model = root / 'model.zip'
            with zipfile.ZipFile(model, 'w') as archive:
                archive.writestr(prepare['MODEL_NAME'] + '/am/final.mdl', 'weights')
                archive.writestr(prepare['MODEL_NAME'] + '/README', 'model notice')
            output = root / 'game.zip'
            prepare['build_bundle'](app, model, output)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {
                    'app.yaml', 'python/main.py', 'assets/logo.svg', 'sketch/sketch.ino',
                    'python/models/' + prepare['MODEL_NAME'] + '/am/final.mdl',
                    'python/models/' + prepare['MODEL_NAME'] + '/README'})
                self.assertIsNone(archive.testzip())

    def test_verified_cache_needs_no_network(self):
        prepare = self.load()
        function = prepare['download_model']
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / 'model.zip'
            cache.write_bytes(b'model archive')
            with patch.dict(function.__globals__, MODEL_SHA256=hashlib.sha256(cache.read_bytes()).hexdigest()), \
                 patch('urllib.request.urlopen', side_effect=AssertionError('Unexpected network')):
                function(cache)

    def test_invalid_download_is_not_published_as_cache(self):
        prepare = self.load()
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / 'model.zip'
            with patch('urllib.request.urlopen', return_value=io.BytesIO(b'incomplete')):
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    prepare['download_model'](cache)
            self.assertFalse(cache.exists())


if __name__ == '__main__':
    unittest.main()
