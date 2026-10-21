# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 SajithDL / Ceyblox
"""Image processing worker. This module has no Blender dependency."""

import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import time


def geometry(source, target, mode):
    sw, sh = source
    tw, th = target
    if mode == 'STRETCH':
        return (tw, th), (0, 0)
    ratio = (min if mode == 'FIT' else max)(tw / sw, th / sh)
    size = (max(1, round(sw * ratio)), max(1, round(sh * ratio)))
    return size, ((tw - size[0]) // 2, (th - size[1]) // 2)


def uv_transform(source, target, mode):
    size, offset = geometry(source, target, mode)
    # Image coordinates start at the top; UV coordinates start at the bottom.
    return [size[0] / target[0], size[1] / target[1],
            offset[0] / target[0], (target[1] - size[1] - offset[1]) / target[1]]


def resize_image(image, target, mode, role):
    from PIL import Image
    import numpy as np

    size, offset = geometry(image.size, target, mode)
    pixels = np.asarray(image.convert('RGBA'), dtype=np.float32) / 255.0
    if role == 'COLOR':
        rgb = pixels[:, :, :3]
        pixels[:, :, :3] = np.where(rgb <= .04045, rgb / 12.92,
                                    ((rgb + .055) / 1.055) ** 2.4)
        pixels[:, :, :3] *= pixels[:, :, 3:4]
    channels = [np.asarray(Image.fromarray(pixels[:, :, c]).resize(
        size, Image.Resampling.LANCZOS), dtype=np.float32) for c in range(4)]
    result = np.stack(channels, axis=2)
    if role == 'COLOR':
        alpha = np.clip(result[:, :, 3:4], 0, 1)
        rgb = result[:, :, :3] / np.maximum(alpha, 1e-8)
        rgb = np.clip(rgb, 0, 1)
        result[:, :, :3] = np.where(rgb <= .0031308, rgb * 12.92,
                                    1.055 * rgb ** (1 / 2.4) - .055)
    elif role == 'NORMAL':
        xyz = result[:, :, :3] * 2 - 1
        length = np.linalg.norm(xyz, axis=2, keepdims=True)
        xyz = np.divide(xyz, np.maximum(length, 1e-8))
        result[:, :, :3] = xyz * .5 + .5
    resized = Image.fromarray(np.uint8(np.clip(result, 0, 1) * 255 + .5))
    fill = (128, 128, 255, 0) if role == 'NORMAL' else (0, 0, 0, 0)
    canvas = Image.new('RGBA', target, fill)
    canvas.paste(resized, offset)
    return canvas


def encode_dds(image, compression, mipmaps, role):
    """Encode each mip with Pillow, then write one standard DDS mip chain."""
    levels = []
    level = image
    while True:
        stream = io.BytesIO()
        mode = 'RGB' if compression in {'DXT1', 'BC5'} else 'RGBA'
        options = {} if compression == 'RGBA8' else {'pixel_format': compression}
        level.convert(mode).save(stream, format='DDS', **options)
        data = stream.getvalue()
        header_size = 148 if data[84:88] == b'DX10' else 128
        levels.append((data[:header_size], data[header_size:]))
        if not mipmaps or level.size == (1, 1):
            break
        size = (max(1, level.width // 2), max(1, level.height // 2))
        level = resize_image(level, size, 'STRETCH', role)
    header = bytearray(levels[0][0])
    flags = struct.unpack_from('<I', header, 8)[0]
    if compression != 'RGBA8':
        struct.pack_into('<I', header, 20, len(levels[0][1]))
    if len(levels) > 1:
        flags |= 0x20000  # DDSD_MIPMAPCOUNT
        struct.pack_into('<I', header, 108, 0x1000 | 0x8 | 0x400000)
    struct.pack_into('<I', header, 8, flags)
    struct.pack_into('<I', header, 28, len(levels))
    if compression == 'BC5':
        struct.pack_into('<I', header, 128, 83)  # DXGI_FORMAT_BC5_UNORM
        struct.pack_into('<I', header, 140, 1)   # one texture, not an array of zero
    return bytes(header) + b''.join(payload for _, payload in levels)


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def process(job):
    from PIL import Image

    source, destination = Path(job['source']), Path(job['destination'])
    if source.resolve() == destination.resolve():
        raise ValueError('Output would replace the source texture. Choose another folder.')
    if destination.exists() and not job.get('overwrite'):
        raise FileExistsError('Output exists; enable overwrite or choose a new folder.')
    with source.open('rb') as stream:
        header = stream.read(148)
    if header.startswith(b'\x89PNG') and len(header) > 24 and header[24] > 8:
        raise ValueError('16-bit PNG input is not supported in this 8-bit workflow.')
    if header.startswith(b'DDS '):
        if struct.unpack_from('<I', header, 112)[0] & (0x200 | 0x200000):
            raise ValueError('Cubemap and volume DDS files are not supported.')
        if header[84:88] == b'DX10':
            dxgi, dimension, misc, array, _ = struct.unpack_from('<5I', header, 128)
            if array != 1 or dimension != 3 or misc & 4:
                raise ValueError('DDS arrays, cubemaps and volumes are not supported.')
            if dxgi in {2, 6, 10, 16, 26, 34, 41, 54, 95, 96}:
                raise ValueError('Floating-point / HDR DDS input is not supported.')
    with Image.open(source) as opened:
        if getattr(opened, 'n_frames', 1) > 1:
            raise ValueError('Animated and multipage images must be converted to a still image first.')
        if opened.mode in {'F', 'I', 'I;16', 'I;16B', 'I;16L'} or opened.info.get('bits', 8) > 8:
            raise ValueError('High bit-depth input is not supported in this 8-bit workflow.')
        if opened.format == 'TIFF' and max(opened.tag_v2.get(258, (8,))) > 8:
            raise ValueError('High bit-depth TIFF input is not supported.')
        if opened.info.get('icc_profile') and job['role'] == 'COLOR':
            from PIL import ImageCms
            profile = ImageCms.ImageCmsProfile(io.BytesIO(opened.info['icc_profile']))
            image = ImageCms.profileToProfile(opened.convert('RGBA'), profile,
                                             ImageCms.createProfile('sRGB'), outputMode='RGBA')
        else:
            image = opened.convert('RGBA')
        original_size = image.size
        image.load()
        opened.close()
    warnings = []
    transform = [1, 1, 0, 0]
    if job.get('resize'):
        target = tuple(job['size'])
        if min(target) < 1 or max(target) > 8192:
            raise ValueError('Dimensions must be between 1 and 8192.')
        transform = uv_transform(image.size, target, job['fit'])
        image = resize_image(image, target, job['fit'], job['role'])
    alpha = image.getchannel('A').getextrema()[0] < 255
    if job.get('alpha') == 'DISCARD':
        image.putalpha(255)
        alpha = False
    compression = job.get('compression', 'AUTO')
    if compression == 'AUTO':
        compression = 'RGBA8' if job['role'] != 'COLOR' else ('DXT5' if alpha or job.get('alpha') == 'PRESERVE' else 'DXT1')
    if compression == 'DXT1' and (alpha or job.get('alpha') == 'PRESERVE'):
        raise ValueError('BC1 would discard alpha. Choose BC3/DXT5 or RGBA8, or explicitly discard alpha.')
    if compression == 'BC5' and job['role'] != 'NORMAL':
        raise ValueError('BC5 is restricted to explicitly identified normal maps.')
    if compression == 'BC5' and alpha:
        raise ValueError('BC5 drops blue and alpha. Explicitly discard alpha only if your shader permits it.')
    fmt = job['format'].upper()
    if fmt == 'DDS' and compression in {'DXT1', 'DXT3', 'DXT5', 'BC5'}:
        if image.width % 4 or image.height % 4:
            raise ValueError('Compressed DDS base dimensions must be multiples of 4. Resize or use RGBA8.')
    if fmt in {'JPEG', 'BMP'} and (alpha or job.get('alpha') == 'PRESERVE'):
        raise ValueError('This output cannot preserve alpha. Choose PNG/TGA/TIFF/DDS or explicitly discard alpha.')
    if fmt == 'DDS' and compression == 'BC5':
        warnings.append('BC5 stores only red and green; verify target shader reconstruction support.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.ceyblox-' + job.get('token', '') + '-', dir=destination.parent)
    os.close(handle)
    try:
        if fmt == 'DDS':
            Path(temporary).write_bytes(encode_dds(image, compression, job.get('mipmaps', True), job['role']))
        else:
            saved = image.convert('RGB') if fmt in {'JPEG', 'BMP'} else image
            saved.save(temporary, format=fmt, **({'quality': 95, 'subsampling': 0} if fmt == 'JPEG' else {}))
        with Image.open(temporary) as check:
            check.load()
            if check.size != image.size:
                raise ValueError('Encoded image failed its dimension check.')
            check.close()
        if job.get('overwrite'):
            os.replace(temporary, destination)
        else:
            # Windows rename refuses an existing destination; POSIX needs an exclusive link.
            if os.name == 'nt':
                os.rename(temporary, destination)
            else:
                os.link(temporary, destination)
        return dict(key=job['key'], output=str(destination), size=list(image.size),
                    source_size=list(original_size), transform=transform,
                    compression=compression if fmt == 'DDS' else fmt,
                    sha256=digest(destination), role=job['role'], warnings=warnings)
    finally:
        Path(temporary).unlink(missing_ok=True)


def run(request, response):
    jobs = json.loads(Path(request).read_text(encoding='utf-8'))
    results = []
    for job in jobs:
        try:
            results.append({'ok': True, **process(job)})
        except Exception as error:
            results.append({'ok': False, 'key': job['key'], 'error': str(error)})
        temporary = Path(str(response) + '.tmp')
        temporary.write_text(json.dumps(results, indent=2), encoding='utf-8')
        for attempt in range(50):
            try:
                os.replace(temporary, response)
                break
            except PermissionError:
                # Windows readers briefly lock the previous progress snapshot.
                if attempt == 49:
                    raise
                time.sleep(.02)


if __name__ == '__main__':
    if '--deps' in sys.argv:
        index = sys.argv.index('--deps')
        sys.path.insert(0, sys.argv[index + 1])
        del sys.argv[index:index + 2]
    if sys.argv[1:] == ['--check']:
        import PIL
        import numpy
        if tuple(int(x) for x in PIL.__version__.split('.')[:3]) < (11, 2, 1):
            raise RuntimeError('Pillow 11.2.1 or newer is required.')
        print('Image engine ready: Pillow ' + PIL.__version__)
    else:
        run(*sys.argv[1:])
