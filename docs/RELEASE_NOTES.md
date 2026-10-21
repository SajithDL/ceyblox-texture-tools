# Ceyblox Texture Tools 1.0.0 Beta 4

Created by **SajithDL / Ceyblox** to make bulk texture preparation easier for FiveM and RedM projects.

Select multiple Blender mesh objects, scan their textures, convert to real DDS files, and relink the materials. Resize separately or combine resizing and conversion in one action.

This version reorganizes the CBT Tools panel into separate collapsible sections, adds a compact scrolling texture list, and moves creator credit and version information to the bottom.

Included:

- DDS, PNG, JPEG, TGA, BMP and TIFF output.
- BC1/DXT1, BC2/DXT3, BC3/DXT5, RGBA8 and advanced BC5 options.
- Alpha preservation, mipmaps and per-texture purpose/compression settings.
- Stretch, Fit Inside and Fill, with square and rectangular size presets.
- Guarded experimental UV adjustment for compatible Fit/Fill cases.
- Nested node-group discovery, selected-object material isolation and restore tools.
- Saved export folder, conversion manifests, checksum validation, progress and logs.

**Install:** download the named addon ZIP below, then use Blender → Preferences → Add-ons → Install from Disk. Open N-panel → CBT Tools → Check Engine; install the engine if needed.

**Compatibility:** Windows x64, Blender 4.2+. Tested with Blender 5.2.2 LTS. FiveM/RedM game shader compatibility is not yet verified in game. BC5 requires explicit shader support. Read the README before using experimental UV changes.

Original textures are retained. Keep a backup of your project; exported files are not covered by Blender Undo.
