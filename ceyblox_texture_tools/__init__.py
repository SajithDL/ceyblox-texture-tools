# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 SajithDL / Ceyblox
"""Ceyblox Texture Tools — batch texture preparation for Blender."""

bl_info = {
    'name': 'Ceyblox Texture Tools',
    'author': 'SajithDL / Ceyblox',
    'version': (1, 0, 0),
    'blender': (4, 2, 0),
    'location': 'View3D > Sidebar > CBT Tools',
    'description': 'Batch convert, resize and relink material textures for FiveM / RedM workflows',
    'category': 'Material',
    'doc_url': 'https://github.com/SajithDL/ceyblox-texture-tools',
}

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty

from .engine import digest

FORMATS = [('DDS', 'DDS', 'Game texture container'), ('PNG', 'PNG', 'Lossless RGBA'),
           ('JPEG', 'JPEG', 'Lossy; no alpha'), ('TGA', 'TGA', 'RGBA'),
           ('BMP', 'BMP', 'RGB; no alpha'), ('TIFF', 'TIFF', 'Lossless RGBA')]
COMPRESSION = [('AUTO', 'Auto (safe)', 'BC1/BC3 for color; uncompressed RGBA8 for data and normals'),
               ('DXT1', 'BC1 / DXT1 — opaque', 'Small; discards alpha'),
               ('DXT3', 'BC2 / DXT3 — alpha', 'Explicit alpha'),
               ('DXT5', 'BC3 / DXT5 — alpha', 'Smooth alpha'),
               ('RGBA8', 'RGBA8 — uncompressed', 'Preserve all four 8-bit channels'),
               ('BC5', 'BC5 — advanced normals', 'RG only; requires a compatible shader; verify in game')]
ROLES = [('COLOR', 'Color (sRGB)', 'Linear-light resizing with alpha-aware filtering'),
         ('DATA', 'Packed / data', 'Filter channels independently; no gamma conversion'),
         ('NORMAL', 'Normal', 'Renormalize vectors after resizing; preserve alpha separately')]
EXTENSIONS = {'DDS': '.dds', 'PNG': '.png', 'JPEG': '.jpg', 'TGA': '.tga', 'BMP': '.bmp', 'TIFF': '.tif'}
SUPPORTED = {'.dds', '.png', '.jpg', '.jpeg', '.tga', '.bmp', '.tif', '.tiff', '.webp', '.avif'}
BUSY = False
ACTIVE_TASK = None


def prefs(context):
    return context.preferences.addons[__package__].preferences


def python_path(context):
    custom = prefs(context).python_path
    if custom:
        return bpy.path.abspath(custom)
    root = Path(sys.prefix)
    candidates = [root / 'bin' / 'python.exe', root / 'bin' / 'python3', root / 'python.exe']
    candidates.extend(sorted((root / 'bin').glob('python3.*')))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError('Bundled Python not found. Set Python executable in addon preferences.')


def deps_path():
    root = bpy.utils.user_resource('CONFIG', path='ceyblox_texture_tools', create=False)
    return str(Path(root) / ('py%d%d' % sys.version_info[:2]))


def worker_command(context):
    return [python_path(context), str(Path(__file__).with_name('engine.py')), '--deps', deps_path()]


def launch(command, log):
    return subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def selected_meshes(context):
    objects = [obj for obj in context.selected_objects if obj.type == 'MESH']
    if not objects:
        raise ValueError('Select at least one mesh object.')
    if context.mode != 'OBJECT':
        raise ValueError('Switch to Object Mode first.')
    return objects


def image_nodes(tree, visited=None):
    if not tree:
        return
    visited = set() if visited is None else visited
    if tree.as_pointer() in visited:
        return
    visited = visited | {tree.as_pointer()}
    for node in tree.nodes:
        if node.type == 'TEX_IMAGE' and node.image:
            yield node
        elif node.type == 'GROUP' and node.node_tree:
            yield from image_nodes(node.node_tree, visited)


def collect(objects):
    found = {}
    seen_nodes = set()
    for obj in objects:
        for slot in obj.material_slots:
            if not slot.material or not slot.material.use_nodes:
                continue
            for node in image_nodes(slot.material.node_tree):
                if node.as_pointer() in seen_nodes:
                    continue
                seen_nodes.add(node.as_pointer())
                key = node.image.as_pointer()
                if key not in found:
                    found[key] = (node.image, [])
                found[key][1].append(node)
    return list(found.values())


def source_path(image):
    return Path(bpy.path.abspath(image.filepath, library=image.library)).resolve()


def image_key(image):
    source = str(source_path(image)) if image.filepath and not image.packed_file else 'packed:' + image.name_full
    return hashlib.sha256((os.path.normcase(source) + '|' + image.colorspace_settings.name
                           + '|' + image.alpha_mode).encode()).hexdigest()


def stem(image):
    name = Path(image.filepath).stem if image.filepath else Path(image.name).stem
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .') or 'texture'
    if name.upper() in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[1-9]', name.upper()):
        name = 'texture_' + name
    return name[:180]


def infer_role(image, nodes):
    name = stem(image).lower()
    if re.search(r'(^|[_ .-])(normal|norm|nrm|nor|n)($|[_ .-])', name):
        return 'NORMAL'
    if image.alpha_mode == 'CHANNEL_PACKED':
        return 'DATA'
    for node in nodes:
        pending = [node]
        seen = set()
        while pending:
            current = pending.pop()
            if current.as_pointer() in seen:
                continue
            seen.add(current.as_pointer())
            for output in current.outputs:
                for link in output.links:
                    if link.to_node.type == 'NORMAL_MAP':
                        return 'NORMAL'
                    if link.to_node.type in {'REROUTE', 'SEPRGB', 'SEPARATE_COLOR'}:
                        pending.append(link.to_node)
    if image.colorspace_settings.name not in {'sRGB', 'Utility - sRGB - Texture'}:
        return 'DATA'
    if re.search(r'(^|[_ .-])(spec|specular|roughness|metallic|mask|orm|rma|ao)($|[_ .-])', name):
        return 'DATA'
    return 'COLOR'


def isolate(objects):
    """Selected objects get private materials and groups before any node changes."""
    materials, groups = {}, {}

    def copy_groups(tree, visited=None):
        visited = set() if visited is None else visited
        if tree.as_pointer() in visited:
            return
        visited.add(tree.as_pointer())
        for node in tree.nodes:
            if node.type == 'GROUP' and node.node_tree:
                original = node.node_tree
                key = original.as_pointer()
                if key not in groups:
                    groups[key] = original.copy()
                    copy_groups(groups[key], visited)
                node.node_tree = groups[key]

    for obj in objects:
        if obj.library or obj.data.library:
            raise ValueError('Make linked objects and mesh data local before relinking.')
    for obj in objects:
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        for slot in obj.material_slots:
            original = slot.material
            if not original or not original.use_nodes:
                continue
            key = original.as_pointer()
            if key not in materials:
                materials[key] = original.copy()
                copy_groups(materials[key].node_tree)
            slot.material = materials[key]


def manifest_path(folder):
    return Path(folder) / 'ceyblox_manifest.json'


def read_manifest(folder):
    path = manifest_path(folder)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != 1:
        raise ValueError('Unsupported conversion manifest version.')
    return data['textures']


def write_manifest(folder, entries):
    target = manifest_path(folder)
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps({'schema': 1, 'textures': entries}, indent=2), encoding='utf-8')
    os.replace(temporary, target)


def log_results(lines):
    text = bpy.data.texts.get('Ceyblox Texture Tools — Log') or bpy.data.texts.new('Ceyblox Texture Tools — Log')
    text.clear()
    text.write('\n'.join(lines))


def validate_output(entry, folder):
    path = Path(entry['output']).resolve()
    if path.parent != Path(folder).resolve() or not path.is_file():
        raise ValueError('Manifest output is missing or outside the chosen folder.')
    if digest(path) != entry['sha256']:
        raise ValueError('Output changed after conversion. Convert again before linking.')
    return path


def preflight_uv(objects, records, entries):
    transforms = {tuple(entry['transform']) for entry in entries.values()}
    if len(transforms) != 1:
        raise ValueError('UV adjustment requires the same transform for every texture.')
    for obj in objects:
        if obj.library or obj.data.library:
            raise ValueError('Experimental UV adjustment requires local meshes.')
        if not obj.data.uv_layers.active:
            raise ValueError(obj.name + ' has no active UV map.')
        if not obj.data.uv_layers.active.active_render:
            raise ValueError('The active UV map must also be the render UV map.')
        if any(v.uv.x < 0 or v.uv.x > 1 or v.uv.y < 0 or v.uv.y > 1
               for v in obj.data.uv_layers.active.data):
            raise ValueError('Experimental UV adjustment requires UVs inside the 0–1 tile.')
        if obj.data.uv_layers.get('CB_UV_Backup'):
            raise ValueError('Restore the previous UV backup before another UV adjustment.')
    for image, nodes in records:
        entry = entries.get(image_key(image))
        if not entry:
            raise ValueError('Every image must be included and successfully converted for UV adjustment.')
        for node in nodes:
            if node.id_data.bl_idname != 'ShaderNodeTree':
                raise ValueError('Unsupported shader tree.')
            if node.extension != 'CLIP':
                raise ValueError('Set image Extension to Clip; repeating textures cannot be padded safely.')
            if node.inputs['Vector'].is_linked:
                raise ValueError('Experimental UV adjustment requires unconnected image Vector inputs.')
    # Nested groups can hide coordinate transforms; refuse them even if image inputs are unlinked.
    for obj in objects:
        for slot in obj.material_slots:
            if slot.material and slot.material.use_nodes:
                if any(n.type == 'GROUP' for n in slot.material.node_tree.nodes):
                    raise ValueError('Experimental UV adjustment does not support node groups.')
    return next(iter(transforms))


def relink(objects, entries, folder, adjust_uv=False):
    records = collect(objects)
    used = {image_key(image): entries[image_key(image)] for image, _ in records if image_key(image) in entries}
    if not used:
        raise ValueError('No converted images match the selected objects. Scan and convert first.')
    for entry in used.values():
        validate_output(entry, folder)
    changes_uv = any(tuple(e['transform']) != (1, 1, 0, 0) for e in used.values())
    if changes_uv and not adjust_uv:
        raise ValueError('Fit/Fill changed image coordinates. Enable experimental UV adjustment or export without linking.')
    transform = preflight_uv(objects, records, used) if changes_uv else None
    # Load first, so unsupported DDS decoding cannot leave half the nodes relinked.
    loaded = {}
    try:
        for image, nodes in records:
            key = image_key(image)
            if key not in used:
                continue
            entry = used[key]
            new_image = bpy.data.images.load(str(validate_output(entry, folder)), check_existing=False)
            loaded[key] = new_image
            new_image.colorspace_settings.name = image.colorspace_settings.name
            new_image.alpha_mode = image.alpha_mode
            new_image.source = 'FILE'
            new_image.reload()
            decoded_size = tuple(new_image.size)
            if decoded_size != tuple(entry['size']) or not new_image.has_data:
                raise ValueError('Blender could not decode ' + new_image.name)
    except Exception:
        for image in loaded.values():
            bpy.data.images.remove(image)
        raise
    isolate(objects)
    count = 0
    for image, nodes in collect(objects):
        key = image_key(image)
        if key not in loaded:
            continue
        image.use_fake_user = True
        for node in nodes:
            if 'cb_original_image' not in node:
                node['cb_original_image'] = image.name
            node.image = loaded[key]
            count += 1
    if transform:
        sx, sy, ox, oy = transform
        for obj in objects:
            mesh = obj.data
            active = mesh.uv_layers.active
            original_index = mesh.uv_layers.active_index
            backup = mesh.uv_layers.new(name='CB_UV_Backup', do_init=True)
            mesh['cb_uv_original_name'] = active.name
            for old, saved in zip(active.data, backup.data):
                saved.uv = old.uv
                old.uv = (old.uv.x * sx + ox, old.uv.y * sy + oy)
            mesh.uv_layers.active_index = original_index
    return count


class CBPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__
    output_folder: StringProperty(name='Default export folder', subtype='DIR_PATH')
    python_path: StringProperty(name='Python executable (optional)', subtype='FILE_PATH',
                               description='Leave empty to use Blender bundled Python')

    def draw(self, context):
        self.layout.prop(self, 'output_folder')
        self.layout.prop(self, 'python_path')
        self.layout.label(text='Install Pillow + NumPy once for this Blender Python version.')
        self.layout.operator('ceyblox.install_engine')


class CBTexture(bpy.types.PropertyGroup):
    image: PointerProperty(type=bpy.types.Image)
    enabled: BoolProperty(name='Include', default=True)
    role: EnumProperty(name='Purpose', items=ROLES)
    compression: EnumProperty(name='DDS override', items=[('DEFAULT', 'Use panel setting', '')] + COMPRESSION)
    key: StringProperty()


class CBSettings(bpy.types.PropertyGroup):
    output_folder: StringProperty(name='Export folder', subtype='DIR_PATH')
    textures: CollectionProperty(type=CBTexture)
    output_format: EnumProperty(name='Output', items=FORMATS, default='DDS')
    compression: EnumProperty(name='DDS format', items=COMPRESSION, default='AUTO')
    alpha: EnumProperty(name='Alpha', items=[('AUTO', 'Auto', 'Preserve meaningful alpha'),
        ('PRESERVE', 'Preserve', 'Require alpha-capable output'),
        ('DISCARD', 'Discard explicitly', 'Remove alpha, including shader data stored in alpha')], default='AUTO')
    mipmaps: BoolProperty(name='Generate mipmaps', default=True)
    preset: EnumProperty(name='Size', items=[(s, s.replace('x', ' × '), '') for s in
        ['128x128', '256x256', '512x512', '512x256', '1024x1024', '1024x512',
         '2048x2048', '2048x1024', '4096x4096']] + [('CUSTOM', 'Custom', '')], default='1024x1024')
    width: IntProperty(name='Width', default=1024, min=1, max=8192)
    height: IntProperty(name='Height', default=1024, min=1, max=8192)
    fit: EnumProperty(name='Resize mode', items=[
        ('STRETCH', 'Stretch', 'Resize the whole image; UVs stay aligned'),
        ('FIT', 'Fit inside (pad)', 'Keep proportions and add transparent padding'),
        ('FILL', 'Fill (crop)', 'Keep proportions and crop edges; may remove texture content')], default='STRETCH')
    overwrite: BoolProperty(name='Overwrite exported files', default=False)
    auto_link: BoolProperty(name='Link after processing', default=False)
    experimental_uv: BoolProperty(name='Experimental: adjust UVs for Fit / Fill', default=False)
    active_texture_index: IntProperty(name='Active texture', default=0, min=0)
    status: StringProperty(default='Select mesh objects, then Scan Textures.')


class CB_OT_install(bpy.types.Operator):
    bl_idname = 'ceyblox.install_engine'
    bl_label = 'Install / Update Image Engine'
    bl_description = 'Download Pillow and NumPy from PyPI into a separate user configuration folder'

    @classmethod
    def poll(cls, context):
        return not BUSY

    def execute(self, context):
        global BUSY, ACTIVE_TASK
        try:
            if not bpy.app.online_access:
                raise ValueError('Enable Allow Online Access in Blender System preferences to install the engine.')
            self._temporary = tempfile.TemporaryDirectory(prefix='ceyblox-setup-')
            self._log = open(Path(self._temporary.name) / 'install.log', 'w', encoding='utf-8')
            command = [python_path(context), '-m', 'pip', 'install', '--upgrade', '--only-binary=:all:',
                       '--disable-pip-version-check', '--target', deps_path(), 'Pillow>=11.2.1,<14', 'numpy>=1.26,<3']
            self._process = launch(command, self._log)
        except Exception as error:
            if hasattr(self, '_log'):
                self._log.close()
            if hasattr(self, '_temporary'):
                self._temporary.cleanup()
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        BUSY = True
        ACTIVE_TASK = self
        self._timer = context.window_manager.event_timer_add(.5, window=context.window)
        context.window_manager.modal_handler_add(self)
        context.scene.ceyblox.status = 'Installing image engine…'
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        global BUSY, ACTIVE_TASK
        if event.type == 'ESC':
            self._process.terminate()
        if self._process.poll() is None:
            return {'PASS_THROUGH'}
        self._log.close()
        log_results([Path(self._temporary.name, 'install.log').read_text(encoding='utf-8', errors='replace')])
        success = self._process.returncode == 0
        context.scene.ceyblox.status = 'Image engine installed.' if success else 'Engine setup failed. See Log.'
        context.window_manager.event_timer_remove(self._timer)
        self._temporary.cleanup()
        BUSY = False
        ACTIVE_TASK = None
        if context.area:
            context.area.tag_redraw()
        return {'FINISHED'} if success else {'CANCELLED'}


class CB_OT_scan(bpy.types.Operator):
    bl_idname = 'ceyblox.scan'
    bl_label = 'Scan Selected Meshes'
    bl_description = 'Find all image textures, including nested node groups; reset per-image overrides'

    @classmethod
    def poll(cls, context):
        return not BUSY

    def execute(self, context):
        settings = context.scene.ceyblox
        try:
            objects = selected_meshes(context)
            records = collect(objects)
            settings.textures.clear()
            settings.active_texture_index = 0
            for image, nodes in records:
                item = settings.textures.add()
                item.image = image
                item.key = image_key(image)
                item.role = infer_role(image, nodes)
            if not settings.output_folder:
                settings.output_folder = prefs(context).output_folder
            settings.status = f'{len(objects)} meshes · {len(records)} unique images'
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class CB_OT_save_folder(bpy.types.Operator):
    bl_idname = 'ceyblox.save_folder'
    bl_label = 'Save Folder as Default'

    def execute(self, context):
        folder = context.scene.ceyblox.output_folder
        if not folder.strip():
            return {'CANCELLED'}
        prefs(context).output_folder = bpy.path.abspath(folder)
        bpy.ops.wm.save_userpref()
        self.report({'INFO'}, 'Default folder saved in Blender preferences.')
        return {'FINISHED'}


class CB_OT_check_engine(bpy.types.Operator):
    bl_idname = 'ceyblox.check_engine'
    bl_label = 'Check Image Engine'

    @classmethod
    def poll(cls, context):
        return not BUSY

    def execute(self, context):
        try:
            result = subprocess.run(worker_command(context) + ['--check'], capture_output=True,
                                    text=True, timeout=20,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise ValueError('Image engine missing. Click Install / Update Image Engine.')
            context.scene.ceyblox.status = result.stdout.strip()
            self.report({'INFO'}, result.stdout.strip())
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class CB_OT_process(bpy.types.Operator):
    bl_idname = 'ceyblox.process'
    bl_label = 'Process Textures'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=[('CONVERT', 'Convert', ''), ('RESIZE', 'Resize', ''), ('BOTH', 'Both', '')])

    @classmethod
    def poll(cls, context):
        return not BUSY

    def execute(self, context):
        global BUSY, ACTIVE_TASK
        settings = context.scene.ceyblox
        try:
            self._objects = selected_meshes(context)
            self._settings = settings
            if not settings.output_folder.strip():
                raise ValueError('Choose an export folder first.')
            self._folder = bpy.path.abspath(settings.output_folder)
            self._entries = read_manifest(self._folder)
            command = worker_command(context)
            check = subprocess.run(command + ['--check'], capture_output=True, text=True,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), timeout=20)
            if check.returncode:
                raise ValueError('Image engine unavailable. Click Install / Update Image Engine first.')
            self._temporary = tempfile.TemporaryDirectory(prefix='ceyblox-batch-')
            self._token = hashlib.sha256(self._temporary.name.encode()).hexdigest()[:20]
            overrides = {item.image.as_pointer(): item for item in settings.textures if item.image}
            records = collect(self._objects)
            jobs, skipped, seen_keys, names = [], [], set(), {}
            for key, entry in self._entries.items():
                names[Path(entry['output']).name.casefold()] = key
            self._auto_link = settings.auto_link
            self._adjust_uv = settings.experimental_uv
            for image, nodes in records:
                item = overrides.get(image.as_pointer())
                if item and not item.enabled:
                    continue
                if image.source not in {'FILE'} or image.is_dirty or image.is_float:
                    skipped.append(image.name + ': save as an 8-bit still image first (dirty/generated/tiled/float image).')
                    continue
                key = image_key(image)
                role = item.role if item else infer_role(image, nodes)
                compression = item.compression if item and item.compression != 'DEFAULT' else settings.compression
                if key in seen_keys:
                    previous = next(job for job in jobs if job['key'] == key)
                    if (previous['role'], previous['compression']) != (role, compression):
                        raise ValueError('One source image has conflicting roles. Save separate source files for these uses.')
                    continue
                fmt = settings.output_format
                if self.action == 'RESIZE':
                    extension = Path(image.filepath).suffix.lower()
                    fmt = next((f for f, ext in EXTENSIONS.items() if ext == extension), None)
                    fmt = {'.jpeg': 'JPEG', '.tiff': 'TIFF'}.get(extension, fmt)
                    if not fmt:
                        skipped.append(image.name + ': Resize Only supports DDS, PNG, JPG, TGA, BMP and TIFF.')
                        continue
                filename = stem(image) + EXTENSIONS[fmt]
                if filename.casefold() in names and names[filename.casefold()] != key:
                    filename = stem(image) + '__' + key[:10] + EXTENSIONS[fmt]
                names[filename.casefold()] = key
                destination = Path(self._folder) / filename
                source = source_path(image)
                if image.packed_file:
                    source = Path(self._temporary.name) / (key + Path(image.filepath or image.name).suffix)
                    source.write_bytes(bytes(image.packed_file.data))
                if not source.is_file():
                    skipped.append(image.name + ': source file not found.')
                    continue
                if settings.preset == 'CUSTOM':
                    size = [settings.width, settings.height]
                else:
                    size = list(map(int, settings.preset.split('x')))
                jobs.append(dict(key=key, source=str(source), destination=str(destination), format=fmt,
                    role=role, compression=compression, alpha=settings.alpha, mipmaps=settings.mipmaps,
                    resize=self.action != 'CONVERT', size=size, fit=settings.fit, overwrite=settings.overwrite,
                    token=self._token))
                seen_keys.add(key)
            if not jobs:
                log_results(skipped or ['No image textures found.'])
                raise ValueError('No textures ready to process. See Log.')
            if self._auto_link and self.action != 'CONVERT' and settings.fit != 'STRETCH':
                if not self._adjust_uv:
                    raise ValueError('Fit/Fill export is supported. Automatic linking requires experimental UV adjustment.')
                if skipped or len(jobs) != len({image_key(im) for im, _ in records}):
                    raise ValueError('UV adjustment requires all material images to be included.')
                from .engine import uv_transform
                trial = {job['key']: {'transform': uv_transform(tuple(im.size), job['size'], job['fit'])}
                         for job in jobs for im, _ in records if image_key(im) == job['key']}
                preflight_uv(self._objects, records, trial)
            self._skipped = skipped
            self._request = Path(self._temporary.name) / 'request.json'
            self._response = Path(self._temporary.name) / 'response.json'
            self._request.write_text(json.dumps(jobs), encoding='utf-8')
            self._log = open(Path(self._temporary.name) / 'worker.log', 'w', encoding='utf-8')
            self._process = launch(command + [str(self._request), str(self._response)], self._log)
            self._total = len(jobs)
            self._cancelled = False
        except Exception as error:
            if hasattr(self, '_log'):
                self._log.close()
            if hasattr(self, '_temporary'):
                self._temporary.cleanup()
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}
        BUSY = True
        ACTIVE_TASK = self
        self._timer = context.window_manager.event_timer_add(.3, window=context.window)
        context.window_manager.modal_handler_add(self)
        context.window_manager.progress_begin(0, self._total)
        settings.status = f'Processing {self._total} textures… Esc to cancel'
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        global BUSY, ACTIVE_TASK
        if event.type == 'ESC' and self._process.poll() is None:
            self._cancelled = True
            self._process.terminate()
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        results = []
        if self._response.exists():
            try:
                results = json.loads(self._response.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                pass
        context.window_manager.progress_update(len(results))
        self._settings.status = f'Processed {len(results)} / {self._total} textures…'
        if context.area:
            context.area.tag_redraw()
        if self._process.poll() is None:
            return {'PASS_THROUGH'}
        self._log.close()
        if self._response.exists():
            results = json.loads(self._response.read_text(encoding='utf-8'))
        for temporary in Path(self._folder).glob('.ceyblox-' + self._token + '-*'):
            temporary.unlink(missing_ok=True)
        lines = list(self._skipped)
        completed = {row['key']: row for row in results if row['ok']}
        for row in results:
            lines.append(('OK: ' + row['output']) if row['ok'] else ('FAILED: ' + row['error']))
            lines.extend(row.get('warnings', []))
        try:
            if completed:
                self._entries.update(completed)
                write_manifest(self._folder, self._entries)
            if self._auto_link and completed and not self._cancelled:
                if self._adjust_uv and len(completed) != self._total:
                    raise ValueError('Some conversions failed. Experimental UV linking was cancelled.')
                count = relink(self._objects, completed, self._folder, self._adjust_uv)
                lines.append(f'Linked {count} image nodes. Original materials and images retained.')
        except Exception as error:
            lines.append('LINK / MANIFEST: ' + str(error))
            self.report({'WARNING'}, str(error))
        if self._process.returncode:
            lines.append(Path(self._temporary.name, 'worker.log').read_text(encoding='utf-8', errors='replace'))
        failures = sum(not r['ok'] for r in results)
        self._settings.status = f'{len(completed)} exported · {failures} failed · {len(self._skipped)} skipped'
        if self._cancelled:
            self._settings.status = 'Cancelled. ' + self._settings.status
        elif len(results) != self._total:
            self._settings.status = 'Worker stopped early. ' + self._settings.status
        log_results(lines)
        context.window_manager.progress_end()
        context.window_manager.event_timer_remove(self._timer)
        self._temporary.cleanup()
        BUSY = False
        ACTIVE_TASK = None
        return {'FINISHED'}


class CB_OT_link(bpy.types.Operator):
    bl_idname = 'ceyblox.link'
    bl_label = 'Link Last Exports'
    bl_options = {'REGISTER', 'UNDO'}
    smart: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        return not BUSY

    def execute(self, context):
        settings = context.scene.ceyblox
        try:
            if not settings.output_folder.strip():
                raise ValueError('Choose an export folder first.')
            folder = bpy.path.abspath(settings.output_folder)
            objects = selected_meshes(context)
            entries = read_manifest(folder)
            if self.smart:
                for image, nodes in collect(objects):
                    key = image_key(image)
                    if key in entries:
                        continue
                    matches = [p for p in Path(folder).iterdir() if p.is_file()
                               and p.stem.casefold() == stem(image).casefold()
                               and p.suffix.lower() in SUPPORTED]
                    dds = [p for p in matches if p.suffix.lower() == '.dds']
                    matches = dds or matches
                    if len(matches) != 1:
                        raise ValueError(image.name + ': missing or ambiguous filename match.')
                    # Unknown external resize history cannot safely adjust UVs.
                    candidate = bpy.data.images.load(str(matches[0]), check_existing=False)
                    try:
                        size = list(candidate.size)
                        if not candidate.has_data or size != list(image.size):
                            raise ValueError(image.name + ': size differs; use a conversion manifest to link resized images.')
                    finally:
                        bpy.data.images.remove(candidate)
                    entries[key] = dict(output=str(matches[0]), sha256=digest(matches[0]),
                                        size=size, transform=[1, 1, 0, 0])
            count = relink(objects, entries, folder, settings.experimental_uv)
            settings.status = f'Linked {count} image nodes.'
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class CB_OT_restore(bpy.types.Operator):
    bl_idname = 'ceyblox.restore'
    bl_label = 'Restore Original Images / UVs'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return not BUSY

    def execute(self, context):
        try:
            objects = selected_meshes(context)
            isolate(objects)
            count = 0
            for image, nodes in collect(objects):
                for node in nodes:
                    name = node.get('cb_original_image')
                    original = bpy.data.images.get(name) if name else None
                    if original:
                        node.image = original
                        del node['cb_original_image']
                        count += 1
            for obj in objects:
                mesh = obj.data
                backup = mesh.uv_layers.get('CB_UV_Backup')
                original = mesh.uv_layers.get(mesh.get('cb_uv_original_name', ''))
                if backup and original:
                    for saved, target in zip(backup.data, original.data):
                        target.uv = saved.uv
                    mesh.uv_layers.remove(backup)
                    del mesh['cb_uv_original_name']
            context.scene.ceyblox.status = f'Restored {count} image nodes and available UV backups.'
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'}, str(error))
            return {'CANCELLED'}


class CB_OT_log(bpy.types.Operator):
    bl_idname = 'ceyblox.log'
    bl_label = 'Open Log'

    def execute(self, context):
        text = bpy.data.texts.get('Ceyblox Texture Tools — Log')
        if not text:
            self.report({'INFO'}, 'No processing log yet.')
        else:
            context.area.type = 'TEXT_EDITOR'
            context.area.spaces.active.text = text
        return {'FINISHED'}


class CB_UL_textures(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        if not item.image:
            layout.label(text='Image unavailable', icon='ERROR')
            return
        if self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text='', icon='IMAGE_DATA')
            return
        row = layout.row(align=True)
        row.prop(item, 'enabled', text='')
        row.label(text=item.image.name, icon='IMAGE_DATA')


def panel_section(layout, identifier, title, icon, closed=True):
    header, body = layout.panel(identifier, default_closed=closed)
    header.label(text=title, icon=icon)
    if body:
        body.enabled = not BUSY
        body.use_property_split = False
    return body


def labeled_property(layout, settings, name, label):
    column = layout.column(align=True)
    column.label(text=label)
    column.prop(settings, name, text='')


class CB_PT_tools(bpy.types.Panel):
    bl_label = 'Ceyblox Texture Tools'
    bl_idname = 'CB_PT_texture_tools'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'CBT Tools'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.ceyblox
        layout.use_property_split = False
        layout.use_property_decorate = False

        body = panel_section(layout, 'cb_selection', 'Selection & Output', 'FILE_FOLDER', closed=False)
        if body:
            meshes = sum(obj.type == 'MESH' for obj in context.selected_objects)
            body.label(text=f'{meshes} selected mesh' + ('' if meshes == 1 else 'es'), icon='OUTLINER_OB_MESH')
            labeled_property(body, settings, 'output_folder', 'Export folder')
            body.operator('ceyblox.save_folder', text='Save as Default', icon='FILE_TICK')
            body.separator(factor=.4)
            row = body.row()
            row.scale_y = 1.15
            row.operator('ceyblox.scan', text='Scan Selected Meshes', icon='VIEWZOOM')

        count = sum(bool(item.image) for item in settings.textures)
        body = panel_section(layout, 'cb_textures', f'Textures ({count})', 'TEXTURE')
        if body:
            if not settings.textures:
                body.label(text='Scan meshes to list their textures.', icon='INFO')
            else:
                body.template_list('CB_UL_textures', '', settings, 'textures',
                                   settings, 'active_texture_index', rows=4, maxrows=4)
                item = settings.textures[min(settings.active_texture_index, len(settings.textures) - 1)]
                if item.image:
                    box = body.box()
                    box.label(text=item.image.name, icon='IMAGE_DATA')
                    box.label(text=f'{item.image.size[0]} × {item.image.size[1]} pixels')
                    labeled_property(box, item, 'role', 'Texture purpose')
                    labeled_property(box, item, 'compression', 'DDS override')

        body = panel_section(layout, 'cb_conversion', 'Conversion', 'IMAGE_DATA')
        if body:
            labeled_property(body, settings, 'output_format', 'Output format')
            body.separator(factor=.4)
            labeled_property(body, settings, 'compression', 'DDS compression')
            body.prop(settings, 'mipmaps')
            labeled_property(body, settings, 'alpha', 'Alpha handling')

        body = panel_section(layout, 'cb_resize', 'Resize', 'FULLSCREEN_ENTER')
        if body:
            labeled_property(body, settings, 'preset', 'Target size')
            if settings.preset == 'CUSTOM':
                labeled_property(body, settings, 'width', 'Width')
                labeled_property(body, settings, 'height', 'Height')
            labeled_property(body, settings, 'fit', 'Resize mode')
            if settings.fit != 'STRETCH':
                box = body.box()
                box.label(text='Fit / Fill changes', icon='INFO')
                box.label(text='image coordinates.')
                box.prop(settings, 'experimental_uv', text='Experimental UV adjustment')

        body = panel_section(layout, 'cb_run', 'Run', 'PLAY', closed=False)
        if body:
            body.prop(settings, 'overwrite', text='Overwrite exported files')
            body.prop(settings, 'auto_link', text='Link after processing')
            body.separator(factor=.4)
            for action, label in [('CONVERT', 'Convert Only'), ('RESIZE', 'Resize Only'),
                                  ('BOTH', 'Resize + Convert')]:
                row = body.row()
                row.scale_y = 1.2 if action == 'BOTH' else 1.05
                op = row.operator('ceyblox.process', text=label, icon='FILE_REFRESH')
                op.action = action

        body = panel_section(layout, 'cb_linking', 'Link & Restore', 'LINKED')
        if body:
            body.operator('ceyblox.link', text='Link Last Exports', icon='LINKED')
            op = body.operator('ceyblox.link', text='Smart Link by Name', icon='VIEWZOOM')
            op.smart = True
            body.separator(factor=.4)
            body.operator('ceyblox.restore', text='Restore Images / UVs', icon='LOOP_BACK')

        body = panel_section(layout, 'cb_engine', 'Image Engine', 'PREFERENCES')
        if body:
            body.operator('ceyblox.check_engine', text='Check Engine', icon='CHECKMARK')
            body.operator('ceyblox.install_engine', text='Install / Update Engine', icon='IMPORT')

        body = panel_section(layout, 'cb_results', 'Results', 'TEXT')
        if body:
            body.enabled = True
            body.operator('ceyblox.log', text='Open Processing Log', icon='TEXT')

        layout.separator(factor=.6)
        status = layout.box()
        width = max(22, int((context.region.width - 60) / 7))
        for line in textwrap.wrap(settings.status, width=width):
            status.label(text=line)

        layout.separator(factor=.4)
        footer = layout.column(align=True)
        footer.alignment = 'CENTER'
        footer.label(text='SajithDL / Ceyblox')
        footer.label(text='FiveM / RedM · v1.0.0 beta 4')


CLASSES = (CBPreferences, CBTexture, CBSettings, CB_OT_install, CB_OT_scan, CB_OT_save_folder, CB_OT_check_engine,
           CB_OT_process, CB_OT_link, CB_OT_restore, CB_OT_log, CB_UL_textures, CB_PT_tools)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.ceyblox = PointerProperty(type=CBSettings)


def unregister():
    global BUSY, ACTIVE_TASK
    if ACTIVE_TASK:
        task = ACTIVE_TASK
        if task._process.poll() is None:
            task._process.terminate()
            try:
                task._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                task._process.kill()
                task._process.wait(timeout=3)
        task._log.close()
        task._temporary.cleanup()
        bpy.context.window_manager.event_timer_remove(task._timer)
        bpy.context.window_manager.progress_end()
        ACTIVE_TASK = None
        BUSY = False
    del bpy.types.Scene.ceyblox
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
