"""Build the installable Blender extension ZIP using only the standard library."""

import argparse
import hashlib
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(output):
    source = ROOT / 'ceyblox_texture_tools'
    manifest = (source / 'blender_manifest.toml').read_text(encoding='utf-8')
    version = re.search(r'^version\s*=\s*"([^"]+)"', manifest, re.MULTILINE).group(1)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'ceyblox-texture-tools-{version}.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as package:
        for name in ['__init__.py', 'engine.py', 'blender_manifest.toml']:
            package.write(source / name, name)
        for name in ['LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.md', 'CHANGELOG.md']:
            package.write(ROOT / name, name)
        for path in sorted((ROOT / 'docs').glob('*.md')):
            package.write(path, 'docs/' + path.name)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix('.zip.sha256').write_text(f'{checksum}  {archive.name}\n', encoding='ascii')
    print(archive)
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    build(parser.parse_args().output.resolve())
