"""Create a portable distribution without credentials, personal data, or caches."""
from pathlib import Path
import argparse
import hashlib
import json
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('--output', required=True)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
output = Path(args.output).resolve()
with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for path in root.rglob('*'):
        relative = path.relative_to(root)
        if not path.is_file() or set(relative.parts) & {'data', '__pycache__', '.scratch', '.git'} or path.suffix in ('.pyc', '.pyo'):
            continue
        archive.write(path, Path(root.name) / relative)
with zipfile.ZipFile(output) as archive:
    assert not any('/data/' in n or '/.scratch/' in n for n in archive.namelist())
    assert archive.testzip() is None
digest = hashlib.sha256(output.read_bytes()).hexdigest()
output.with_suffix('.sha256').write_text(digest + '  ' + output.name + '\n', encoding='ascii')
print(json.dumps({'archive': output.name, 'size_mb': round(output.stat().st_size / 2**20, 1), 'sha256': digest}))
