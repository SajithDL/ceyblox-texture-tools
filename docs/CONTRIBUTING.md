# Contributing

Ceyblox Texture Tools is maintained by **SajithDL / Ceyblox**.

## Report a problem

Open an issue with your Blender version, operating system, source image format, selected DDS settings and the relevant addon log. Include a small reproducible example when possible. Share only assets you have permission to distribute.

## Work on the addon

Fork the repository, clone your fork, and create a branch for your change. Keep changes focused and describe what changed and how you tested it in your pull request.

```powershell
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

The Blender integration test uses a factory scene and temporary textures:

```powershell
blender --background --factory-startup --python-exit-code 1 --python tests/blender_smoke.py
```

Pillow and NumPy must be available to Blender's Python for that test.

## Build an install ZIP

```powershell
python tools/build_release.py
```

The addon ZIP and SHA-256 checksum are written to `dist/`. Install the named addon ZIP in Blender, rather than the repository's source archive.

Contributions are distributed under GPL-3.0-or-later. Preserve existing copyright and license notices.
