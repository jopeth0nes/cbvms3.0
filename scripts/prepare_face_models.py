"""Install verified official InsightFace assets locally, outside application startup."""
import hashlib
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://github.com/deepinsight/insightface/releases/download/model-zoo/buffalo_l.zip'
SHA256 = '80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f'


def main():
    destination = ROOT / 'models' / 'buffalo_l'
    required = ('det_10g.onnx', 'w600k_r50.onnx')
    with tempfile.TemporaryDirectory(dir=ROOT / 'models') as temporary:
        archive = Path(temporary) / 'buffalo_l.zip'
        print('Downloading official buffalo_l model pack (~275 MB)...', flush=True)
        urllib.request.urlretrieve(URL, archive)
        with archive.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != SHA256:
            raise RuntimeError('Model archive checksum mismatch; installation cancelled.')
        with zipfile.ZipFile(archive) as pack:
            for filename in required:
                matches = [entry for entry in pack.infolist() if Path(entry.filename).name == filename]
                if len(matches) != 1 or matches[0].file_size < 1024:
                    raise RuntimeError('Missing or invalid model in archive: ' + filename)
                with pack.open(matches[0]) as source, (Path(temporary) / filename).open('wb') as output:
                    shutil.copyfileobj(source, output)
        destination.mkdir(parents=True, exist_ok=True)
        for filename in required:
            (Path(temporary) / filename).replace(destination / filename)
    print('Verified detection and recognition models installed in ' + str(destination))


if __name__ == '__main__':
    main()
