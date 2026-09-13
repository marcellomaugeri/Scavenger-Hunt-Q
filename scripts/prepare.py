"""Prepare an App Lab import ZIP, including the offline voice model."""
import hashlib
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import urllib.request
import zipfile

MODEL_NAME = 'vosk-model-small-en-us-0.15'
MODEL_URL = f'https://alphacephei.com/vosk/models/{MODEL_NAME}.zip'
MODEL_SHA256 = '30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498'
ROOT = Path(__file__).resolve().parents[1]


def checksum(file):
    digest = hashlib.sha256()
    with file.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def download_model(cache):
    if cache.is_file() and checksum(cache) == MODEL_SHA256:
        return
    cache.parent.mkdir(parents=True, exist_ok=True)
    print('Downloading the offline voice model (40 MB)…', flush=True)
    with tempfile.TemporaryDirectory(dir=cache.parent) as directory:
        download = Path(directory) / 'model.zip'
        with urllib.request.urlopen(MODEL_URL, timeout=60) as response, download.open('wb') as output:
            shutil.copyfileobj(response, output)
        if checksum(download) != MODEL_SHA256:
            raise ValueError('Voice model checksum mismatch; rerun preparation to retry')
        download.replace(cache)


def build_bundle(app, model, output):
    # Stage the archive so an interrupted build cannot replace a usable bundle.
    with tempfile.TemporaryDirectory(dir=output.parent) as directory:
        staged = Path(directory) / 'app.zip'
        with zipfile.ZipFile(staged, 'w', zipfile.ZIP_DEFLATED) as bundle:
            sources = [app / 'app.yaml']
            for folder in ('python', 'assets', 'sketch'):
                sources.extend(sorted((app / folder).rglob('*')))
            for source in sources:
                relative = source.relative_to(app)
                if any(part.startswith('.') or part == '__pycache__' for part in relative.parts):
                    continue
                if relative.parts[:2] == ('python', 'models') or source.suffix in ('.pyc', '.pyo'):
                    continue
                if source.is_file() and not source.is_symlink():
                    bundle.write(source, relative.as_posix())
            with zipfile.ZipFile(model) as weights:
                for entry in weights.infolist():
                    name = PurePosixPath(entry.filename)
                    if name.is_absolute() or '..' in name.parts or name.parts[0] != MODEL_NAME:
                        raise ValueError('Unexpected voice model archive layout')
                    if not entry.is_dir():
                        with weights.open(entry) as source, bundle.open('python/models/' + name.as_posix(), 'w') as target:
                            shutil.copyfileobj(source, target)
        staged.replace(output)


def main():
    cache = ROOT / '.cache' / (MODEL_NAME + '.zip')
    output = ROOT / 'Scavenger-Hunt-Q.zip'
    download_model(cache)
    build_bundle(ROOT / 'app', cache, output)
    print(f'Ready: {output}\nImport this ZIP in Arduino App Lab, then click Run.')


if __name__ == '__main__':
    main()
