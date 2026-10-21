# Ceyblox Texture Tools

**Batch texture conversion, resizing and material relinking for Blender.**

Created by **SajithDL / Ceyblox** for FiveM and RedM texture workflows.

I made this because preparing textures one by one was slowing down my FiveM work. I wanted to select several objects, export their textures to one folder as real DDS files, and link the materials back to those files without rebuilding the materials.

## Download and install

Download **ceyblox-texture-tools-1.0.0-beta.4.zip** from this project's Releases. Keep the ZIP intact.

1. In Blender, open **Edit → Preferences → Add-ons**.
2. Open the menu at the top right and choose **Install from Disk**.
3. Select the ZIP and enable **Ceyblox Texture Tools**.
4. In the 3D Viewport, press **N**, then open **CBT Tools**.
5. Expand **Image Engine** and click **Check Engine**. If the engine is missing, enable **Allow Online Access** in Blender's System preferences, then click **Install / Update Engine** once. The installer downloads Pillow and NumPy from PyPI into a separate configuration folder. Restart Blender if an engine update requests it or fails to load.

Processing runs locally. Texture files are not uploaded. The network is used only when you explicitly install or update the image engine. No separate DDS encoder or administrator access is required.

**First release:** Windows x64, Blender 4.2 or newer. Tested in Blender **5.2.2 LTS**. Other Blender versions have not yet been tested. FiveM / RedM in-game validation is still needed; game shader and asset requirements vary. This addon prepares image files; it does not export YTDs or game models.

## Quick workflow

The **CBT Tools** panel has independent collapsible sections: **Selection & Output**, **Textures**, **Conversion**, **Resize**, **Run**, **Link & Restore**, **Image Engine** and **Results**. The texture list scrolls within a fixed area; select a texture to see its purpose and DDS override below it. Creator credit and version information appear at the bottom.

1. Save your `.blend` file and original textures.
2. Select all the mesh objects you want to process in **Object Mode**.
3. Choose an **Export folder**. Use **Save Folder as Default** to remember it for future projects.
4. Click **Scan Selected Meshes**. Expand **Textures**, select an image and review its purpose: Color, Packed / Data, or Normal. You can exclude individual images and override their DDS compression.
5. Expand **Run** and choose your operation:
   - **Convert Only:** change file format; keep image dimensions.
   - **Resize Only:** keep the source file format; apply the resize settings.
   - **Resize + Convert:** apply both in one pass.
6. To use the output in your materials, enable **Link after processing**, or click **Link Last Exports** afterward.

Shared image datablocks are processed once. Source images in separate datablocks with the same file, color space and alpha interpretation are deduplicated too. Conflicting per-image settings are refused. Images from different folders with identical names receive a suffix when needed to avoid colliding with another image in the batch or manifest.

**Overwrite** is off by default. An existing output is reported as a failure rather than silently reused. The source file itself is never overwritten, even when overwrite is enabled. Use a separate export folder when processing existing DDS files.

## Image formats

Inputs include ordinary 8-bit PNG, JPEG, TGA, BMP, TIFF and supported DDS textures. WebP and AVIF can also be converted when the installed Pillow build supports their codecs. PNG is the safest intermediate editing format.

Outputs: **DDS, PNG, JPEG, TGA, BMP and TIFF**. DDS can be converted back to these image formats. JPEG and BMP require explicitly discarding meaningful alpha. Resize Only supports the six output formats; use Resize + Convert for other input formats.

This release intentionally refuses dirty images, generated images, UDIMs, animated/multipage images, float/HDR images, cubemaps and DDS arrays. Save unsupported Blender sources as ordinary 8-bit still files first. High bit-depth PNG and TIFF files are rejected to avoid silent precision loss. It does not bake procedural textures or flatten shader effects.

## DDS compression and alpha

| Setting | Intended use |
| --- | --- |
| Auto (safe) | BC1 for opaque color; BC3 for color with alpha; RGBA8 for normals and packed data |
| BC1 / DXT1 | Small opaque textures; alpha is refused unless explicitly discarded |
| BC2 / DXT3 | Color with explicit alpha |
| BC3 / DXT5 | Color with smooth alpha |
| RGBA8 | Uncompressed four-channel output; preserves 8-bit base pixels without compression loss |
| BC5 | Advanced RG normal encoding; stores only red and green; verify shader support |

**Alpha: Auto** keeps meaningful alpha. **Preserve** requires an alpha-capable output even when the current pixels are opaque. **Discard explicitly** removes alpha, including any shader data packed into it. Normal and specular alpha may carry data rather than transparency: keep those channels unless you know the target shader.

DDS alpha support and a material's transparent shader mode are separate. The addon keeps existing node connections and shader settings. It does not automatically change a Sollumz shader or Blender surface mode just because a texture contains alpha.

Full mip chains are enabled by default for DDS. Color mipmaps use linear-light, alpha-aware filtering. Data channels are filtered independently. Normal vectors are renormalized after resizing and when generating mipmaps; their alpha is filtered separately. The base level of an unresized normal is preserved. Green channels are not flipped and channels are not automatically repacked.

BC1, BC2, BC3 and BC5 are lossy. BC5 drops the blue channel and requires the target shader to reconstruct it. RGBA8 is the conservative default for normal/specular/packed images because their channel conventions vary between shaders. We do not label every DDS format as universally FiveM/RedM compatible.

Compressed DDS base dimensions must be multiples of four. The presets use powers of two, including **128×128, 256×256, 512×512, 512×256, 1024×1024, 1024×512, 2048×2048, 2048×1024 and 4096×4096**. Custom dimensions range from 1 to 8192. Smaller mip levels are correctly stored as compression blocks. Large batches at high resolution can use substantial RAM and time.

## Resize modes and UVs

| Mode | Behavior | UV handling |
| --- | --- | --- |
| Stretch | Resize the entire image to the target dimensions | No UV changes needed |
| Fit inside | Preserve proportions and add transparent padding | Matching UV scale and offset required for relinking |
| Fill | Preserve proportions and crop to fill the target | Matching UV transform required; cropped content is lost |

For example, stretching **1420×755 → 1024×1024** keeps the same relative texture placement on the mesh without editing the UVs. It resamples the image unevenly, so detail quality can change. Fit and Fill change where content sits within the image.

**Fit and Fill always support export without relinking.** To relink them automatically, explicitly enable **Experimental: adjust UVs for Fit / Fill**. It is restricted to local meshes with a common transform for every image, all images included and converted successfully, UVs inside the 0–1 tile, the active UV map also marked for rendering, unconnected image Vector inputs, Extension set to **Clip**, and no node groups. Tiled/mapped/shared-coordinate cases are refused rather than guessed. Different aspect ratios among related maps commonly require different transforms and will be refused.

A `CB_UV_Backup` layer is retained before UV edits. **Restore Images / UVs** restores it; restore before another experimental UV pass. Fill can crop visible content even when its UV transform is mathematically correct. Test it on a copy first.

## Relinking and recovery

Relinking changes image references in copied materials and node groups for the selected meshes. Mesh data shared with other objects is copied first. Unselected objects retain their original materials and images. Original images receive a fake user so they remain available for restoration.

**Link Last Exports** uses `ceyblox_manifest.json` to match the source identity, output size, UV transform and output checksum. Keep that file alongside your exports. Files changed after export are refused until converted again. Only successfully processed outputs are automatically linked.

**Smart Link by Name** first uses manifest entries. Otherwise it matches a filename stem without considering the extension, with DDS priority. Ambiguous matches are refused. Without a manifest, it accepts only matching dimensions because it cannot establish an external file's resize history.

**Restore Images / UVs** restores image references and UV backups in the current selected materials. It does not delete exported files or swap entire original materials back into their slots. Do not rename or delete the retained original image datablocks before restoring. Blender Undo covers scene edits; it cannot undo files written to disk.

Press **Esc** while the processing operator is active to stop. Completed exports remain; cancellation does not automatically relink materials. Progress is shown in the panel. **Open Processing Log** opens the results in Blender's Text Editor; use **Shift+F5** to return that area to the 3D Viewport.

## Sollumz

Sollumz remains the game asset/shader tool in the workflow. Its [2.9 release](https://github.com/Sollumz/Sollumz/releases/tag/v2.9.0) adds texture dictionary import/export and gathering textures from mesh sources using Sollumz shaders. Ceyblox Texture Tools focuses on standalone image conversion, resizing and controlled relinking, including ordinary Blender materials. No Sollumz dependency is required, and the addon does not replace its asset export tools.

## Troubleshooting

- **Image engine missing:** run Check, then Install / Update Engine. A custom Python executable can be configured in addon preferences if Blender has no bundled executable. That Python must have compatible Pillow and NumPy packages. Install failures are written to Open Processing Log; no files are renamed as a fallback.
- **Source missing or image skipped:** save the image to a supported still-file format first. Packed still images are extracted to a temporary file and processed without unpacking the originals.
- **Alpha warning:** choose PNG, TGA, TIFF, BC3 or RGBA8, or explicitly discard alpha if it is safe for that texture.
- **Output already exists:** enable overwrite or choose a new folder. This is not a conversion success.
- **Cannot link a DDS:** Blender must be able to decode the chosen DDS format. The output remains available even if loading fails; try RGBA8 or BC3 as appropriate.
- **Texture purpose looks wrong:** purpose detection is a starting suggestion based on node links, names, color space and alpha interpretation. Review it before processing, especially custom game shaders and specular maps.
- **Fit/Fill cannot link:** export only, use Stretch, or satisfy the documented experimental UV restrictions. UV adjustment is not a general UV unwrap or atlas repack tool.

## Development and releases

See [Contributing](docs/CONTRIBUTING.md) for development, testing and building an install ZIP. [Validation notes](docs/VALIDATION.md) record what was tested. [The changelog](CHANGELOG.md) tracks changes.

Licensed under **GPL-3.0-or-later**. Copyright © 2026 **SajithDL / Ceyblox**. Pillow and NumPy are installed separately under their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
