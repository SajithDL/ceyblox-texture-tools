"""Run with blender --background --factory-startup --python tests/blender_smoke.py."""

from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import addon_utils
import bpy
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
addon_utils.enable('ceyblox_texture_tools', default_set=True)
import ceyblox_texture_tools as addon
from ceyblox_texture_tools import engine


def make_mesh(name, material):
    bpy.ops.mesh.primitive_plane_add()
    obj = bpy.context.object
    obj.name = name
    obj.data.materials.append(material)
    return obj


def select(objects):
    bpy.ops.object.select_all(action='DESELECT')
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]


def job(image, output, **changes):
    result = dict(key=addon.image_key(image), source=str(addon.source_path(image)),
                  destination=str(output), format='DDS', role='COLOR', alpha='AUTO',
                  compression='AUTO', mipmaps=True, resize=False, fit='STRETCH', size=[16, 16])
    result.update(changes)
    return result


with tempfile.TemporaryDirectory(prefix='ceyblox-blender-') as temporary:
    root = Path(temporary)
    source = root / 'diffuse.png'
    Image.new('RGBA', (32, 16), (180, 80, 30, 110)).save(source)
    image = bpy.data.images.load(str(source))
    material = bpy.data.materials.new('Shared material')
    material.use_nodes = True
    texture = material.node_tree.nodes.new('ShaderNodeTexImage')
    texture.image = image
    shader = material.node_tree.nodes.get('Principled BSDF')
    material.node_tree.links.new(texture.outputs['Color'], shader.inputs['Base Color'])
    a = make_mesh('Selected A', material)
    b = make_mesh('Selected B', material)
    untouched = make_mesh('Unselected', material)
    select([a, b])
    assert bpy.ops.ceyblox.scan() == {'FINISHED'}
    assert len(bpy.context.scene.ceyblox.textures) == 1
    records = addon.collect([a, b])
    assert len(records) == 1
    destination = root / 'converted' / 'diffuse.dds'
    result = engine.process(job(image, destination))
    addon.write_manifest(destination.parent, {result['key']: result})
    bpy.context.scene.ceyblox.output_folder = str(destination.parent)
    assert bpy.ops.ceyblox.link() == {'FINISHED'}
    assert texture.image == image
    assert untouched.data.materials[0] == material
    for obj in [a, b]:
        assert obj.data.materials[0] != material
        linked = addon.collect([obj])[0][0]
        assert Path(linked.filepath) == destination
        assert linked.colorspace_settings.name == image.colorspace_settings.name
        assert linked.size[:] == (32, 16)
        assert len(obj.data.materials[0].node_tree.links) == len(material.node_tree.links)
    assert bpy.ops.ceyblox.restore() == {'FINISHED'}
    assert addon.collect([a])[0][0] == image
    print('PASS: multi-object deduplication, real DDS, links, isolation, restore')

    # Nested groups are discovered and copied before selected nodes are changed.
    group = bpy.data.node_groups.new('Nested textures', 'ShaderNodeTree')
    group_texture = group.nodes.new('ShaderNodeTexImage')
    group_texture.image = image
    group_node = material.node_tree.nodes.new('ShaderNodeGroup')
    group_node.node_tree = group
    select([untouched])
    addon.relink([untouched], {result['key']: result}, destination.parent)
    assert group_texture.image == image
    assert addon.collect([untouched])[0][0] != image
    print('PASS: nested group discovery and isolation')

    # A deliberately restricted fit workflow gets a reversible UV transform.
    select([a, b])
    for obj in [a, b]:
        for _, nodes in addon.collect([obj]):
            for node in nodes:
                node.extension = 'CLIP'
    original_uvs = [[tuple(item.uv) for item in obj.data.uv_layers.active.data] for obj in [a, b]]
    padded = root / 'fit' / 'diffuse.dds'
    fit_result = engine.process(job(image, padded, resize=True, fit='FIT'))
    try:
        addon.relink([a, b], {fit_result['key']: fit_result}, padded.parent)
        raise AssertionError('Fit should require explicit UV handling')
    except ValueError:
        pass
    addon.relink([a, b], {fit_result['key']: fit_result}, padded.parent, adjust_uv=True)
    for obj in [a, b]:
        assert obj.data.uv_layers.get('CB_UV_Backup')
        values = [tuple(v.uv) for v in obj.data.uv_layers.active.data]
        assert min(v[1] for v in values) == .25
        assert max(v[1] for v in values) == .75
    bpy.ops.ceyblox.restore()
    for obj, expected in zip([a, b], original_uvs):
        assert [tuple(v.uv) for v in obj.data.uv_layers.active.data] == expected
        assert not obj.data.uv_layers.get('CB_UV_Backup')
    print('PASS: guarded Fit UV adjustment and UV restore')

    # Corrupt exports must fail before any material copies or node edits.
    destination.write_bytes(b'not DDS')
    current = a.data.materials[0]
    try:
        addon.relink([a], {result['key']: result}, destination.parent)
        raise AssertionError('Corrupt export should be rejected')
    except ValueError:
        pass
    assert a.data.materials[0] == current
    print('PASS: modified output rejected before relinking')

    command = addon.worker_command(bpy.context)
    check = subprocess.run(command + ['--check'], capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    print('PASS: Blender bundled Python worker:', check.stdout.strip())

    # Exercise the actual asynchronous operator preparation and completion paths.
    select([a, b])
    settings = bpy.context.scene.ceyblox
    settings.output_folder = str(root / 'buttons')
    settings.output_format = 'DDS'
    settings.preset = '128x128'
    settings.fit = 'STRETCH'
    settings.auto_link = False
    for action, expected_size in [('CONVERT', (32, 16)), ('RESIZE', (128, 128)), ('BOTH', (128, 128))]:
        settings.output_folder = str(root / action)
        assert bpy.ops.ceyblox.process(action=action) == {'RUNNING_MODAL'}
        task = addon.ACTIVE_TASK
        deadline = time.monotonic() + 30
        while addon.ACTIVE_TASK and time.monotonic() < deadline:
            task.modal(bpy.context, SimpleNamespace(type='TIMER'))
            time.sleep(.05)
        assert not addon.BUSY, 'Worker timed out'
        assert settings.status.startswith('1 exported'), settings.status
        exports = addon.read_manifest(settings.output_folder)
        assert len(exports) == 1
        entry = next(iter(exports.values()))
        assert tuple(entry['size']) == expected_size
        assert Path(entry['output']).suffix == ('.png' if action == 'RESIZE' else '.dds')
    print('PASS: Convert Only, Resize Only and Resize + Convert operator paths')

    # Batch normal/specular maps, packed images and colliding filename stems.
    normal_path = root / 'normal_n.png'
    spec_path = root / 'other' / 'diffuse.png'
    spec_path.parent.mkdir()
    Image.new('RGBA', (32, 16), (128, 128, 255, 90)).save(normal_path)
    Image.new('RGBA', (32, 16), (10, 60, 180, 70)).save(spec_path)
    normal = bpy.data.images.load(str(normal_path))
    normal.colorspace_settings.name = 'Non-Color'
    normal.pack()
    specular = bpy.data.images.load(str(spec_path))
    specular.colorspace_settings.name = 'Non-Color'
    tree = a.data.materials[0].node_tree
    for extra in [normal, specular]:
        node = tree.nodes.new('ShaderNodeTexImage')
        node.image = extra
    settings.output_folder = str(root / 'maps')
    settings.auto_link = True
    settings.preset = '128x128'
    bpy.ops.ceyblox.scan()
    assert len(settings.textures) == 3
    assert bpy.ops.ceyblox.process(action='BOTH') == {'RUNNING_MODAL'}
    task = addon.ACTIVE_TASK
    deadline = time.monotonic() + 30
    while addon.ACTIVE_TASK and time.monotonic() < deadline:
        task.modal(bpy.context, SimpleNamespace(type='TIMER'))
        time.sleep(.05)
    assert not addon.BUSY
    assert settings.status.startswith('3 exported'), (settings.status,
        bpy.data.texts.get('Ceyblox Texture Tools — Log').as_string())
    outputs = addon.read_manifest(settings.output_folder)
    assert len({e['output'] for e in outputs.values()}) == 3
    assert sorted(e['compression'] for e in outputs.values()) == ['DXT5', 'RGBA8', 'RGBA8']
    assert all(Path(im.filepath).suffix == '.dds' for im, _ in addon.collect([a, b]))
    assert normal.packed_file
    bpy.ops.ceyblox.restore()
    assert {im for im, _ in addon.collect([a])} == {image, normal, specular}
    print('PASS: normal/specular maps, packed image, name collision and auto-link batch')
    print('CEYBLOX_SMOKE_OK', bpy.app.version_string)

addon_utils.disable('ceyblox_texture_tools', default_set=True)
