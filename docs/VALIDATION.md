# Validation — 1.0.0

Tested on Windows x64 on 2026-10-21.

## Image engine

Python 3.10, Pillow 12.0.0 and NumPy 2.2.6: **14 tests passed**.

Coverage includes:

- Real BC1, BC2, BC3, BC5 and uncompressed DDS decoding.
- DDS mip-count headers, complete block payload sizes and single-level output.
- BC5 UNORM header and array-size fields.
- Alpha auto-selection and padded-image alpha.
- Rejection of destructive paths, existing outputs and accidental alpha loss.
- Exact four-channel base-pixel preservation for uncompressed packed data.
- DDS-to-PNG conversion and all six output formats.
- Fit/Fill/Stretch geometry and matching UV transforms.
- Gamma-correct color resizing versus independent data-channel resizing.
- Normal-vector normalization and independent alpha filtering.
- High bit-depth PNG rejection and DDS block-size validation.
- Per-image failure reporting without false conversion success.

## Blender integration

Blender **5.2.2 LTS**, bundled Python 3.13, Pillow 13.0.0 and NumPy 2.3.4: **passed**.

Factory-scene checks cover registration, selected mesh scanning, shared-image deduplication, real DDS loading, material connection retention, preservation of unselected objects, nested group isolation, image restoration, guarded Fit UV adjustment and UV restoration, checksum rejection before relinking, the bundled-Python worker, all three asynchronous processing actions, normal/specular maps, packed images, filename collision handling and automatic relinking.

Blender's extension validator accepted the built install ZIP and its manifest. Tests used temporary fixtures and did not open or save production projects.

## Remaining manual checks

- Visual N-panel layout and interactive cancellation in a normal Blender session.
- First-time image-engine download on a clean Blender installation.
- Blender 4.2/4.5 compatibility; the manifest minimum is 4.2, but only 5.2.2 was executed locally.
- FiveM and RedM in-game tests against representative shaders and exports.
- BC5 loading/interpretation in each target application.
- Complex custom shader graphs, unusual ICC profiles and uncommon codecs.

The standalone engine tests also passed on Windows and Linux in CI. The shipped extension currently targets Windows x64.
