import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('engine', ROOT / 'ceyblox_texture_tools' / 'engine.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / 'source.png'
        image = Image.new('RGBA', (32, 16), (220, 90, 40, 255))
        image.putpixel((4, 4), (20, 100, 200, 70))
        image.save(self.source)

    def tearDown(self):
        self.directory.cleanup()

    def job(self, **changes):
        result = dict(key='test', source=str(self.source), destination=str(self.root / 'output.dds'),
                      format='DDS', role='COLOR', alpha='AUTO', compression='AUTO',
                      mipmaps=True, resize=False, size=[16, 16], fit='STRETCH', overwrite=False)
        result.update(changes)
        return result

    def test_alpha_auto_uses_real_bc3_with_full_mip_chain(self):
        result = engine.process(self.job())
        data = Path(result['output']).read_bytes()
        self.assertEqual(data[:4], b'DDS ')
        self.assertEqual(data[84:88], b'DXT5')
        self.assertEqual(struct.unpack_from('<I', data, 28)[0], 6)
        payload = sum(max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * 16
                      for w, h in [(32, 16), (16, 8), (8, 4), (4, 2), (2, 1), (1, 1)])
        self.assertEqual(len(data), 128 + payload)
        with Image.open(result['output']) as image:
            self.assertLess(image.convert('RGBA').getpixel((4, 4))[3], 180)

    def test_all_dds_formats_decode_and_have_expected_headers(self):
        Image.new('RGBA', (16, 16), (128, 128, 255, 255)).save(self.source)
        for compression in ['DXT1', 'DXT3', 'DXT5', 'RGBA8', 'BC5']:
            with self.subTest(compression=compression):
                result = engine.process(self.job(compression=compression, role='NORMAL', overwrite=True))
                with Image.open(result['output']) as image:
                    image.load()
                    self.assertEqual(image.size, (16, 16))
                if compression == 'BC5':
                    data = Path(result['output']).read_bytes()
                    self.assertEqual(struct.unpack_from('<I', data, 128)[0], 83)
                    self.assertEqual(struct.unpack_from('<I', data, 140)[0], 1)

    def test_data_roundtrip_preserves_all_channels(self):
        rng = np.random.default_rng(22)
        original = rng.integers(0, 256, (16, 32, 4), dtype=np.uint8)
        Image.fromarray(original).save(self.source)
        result = engine.process(self.job(role='DATA'))
        self.assertEqual(result['compression'], 'RGBA8')
        with Image.open(result['output']) as image:
            np.testing.assert_array_equal(original, np.asarray(image.convert('RGBA')))

    def test_refuse_destructive_or_alpha_losing_requests(self):
        for changes in [dict(destination=str(self.source)), dict(compression='DXT1'),
                        dict(format='JPEG'), dict(compression='BC5')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                engine.process(self.job(**changes))
        engine.process(self.job())
        with self.assertRaises(FileExistsError):
            engine.process(self.job())

    def test_fit_and_fill_geometry_matches_uv_transform(self):
        for mode in ['FIT', 'FILL', 'STRETCH']:
            with self.subTest(mode=mode):
                result = engine.process(self.job(resize=True, fit=mode, overwrite=True))
                self.assertEqual(result['size'], [16, 16])
                self.assertEqual(result['transform'], engine.uv_transform((32, 16), (16, 16), mode))
        self.assertEqual(engine.uv_transform((32, 16), (16, 16), 'FIT'), [1, .5, 0, .25])
        self.assertEqual(engine.uv_transform((32, 16), (16, 16), 'FILL'), [2, 1, -.5, 0])

    def test_normal_resize_keeps_unit_vectors_and_independent_alpha(self):
        source = Image.new('RGBA', (32, 16), (170, 120, 240, 80))
        result = engine.resize_image(source, (16, 8), 'STRETCH', 'NORMAL')
        values = np.asarray(result, dtype=np.float32)
        lengths = np.linalg.norm(values[:, :, :3] / 255 * 2 - 1, axis=2)
        self.assertTrue(np.all(np.abs(lengths - 1) < .015))
        self.assertTrue(np.all(values[:, :, 3] == 80))

    def test_dds_to_png_and_resize_only(self):
        dds = engine.process(self.job())
        result = engine.process(self.job(source=dds['output'], destination=str(self.root / 'back.png'),
                                         format='PNG', resize=True))
        with Image.open(result['output']) as image:
            self.assertEqual(image.size, (16, 16))
            self.assertEqual(image.mode, 'RGBA')

    def test_non_multiple_block_size_requires_resize_or_rgba(self):
        Image.new('RGB', (17, 13)).save(self.source)
        with self.assertRaises(ValueError):
            engine.process(self.job())
        result = engine.process(self.job(compression='RGBA8'))
        self.assertEqual(result['size'], [17, 13])

    def test_16bit_png_rejected_before_downconversion(self):
        Image.fromarray(np.full((16, 16), 32768, np.uint16)).save(self.source)
        with self.assertRaises(ValueError):
            engine.process(self.job())

    def test_normal_and_data_alpha_are_not_used_as_filter_weights(self):
        pixels = np.zeros((8, 8, 4), np.uint8)
        pixels[:, :, 0] = 180
        pixels[:, :, 3] = np.arange(8) * 32
        output = engine.resize_image(Image.fromarray(pixels), (4, 4), 'STRETCH', 'DATA')
        self.assertTrue(np.all(np.asarray(output)[:, :, 0] == 180))

    def test_supported_output_formats_and_no_mips(self):
        for fmt, extension in [('PNG', '.png'), ('JPEG', '.jpg'), ('TGA', '.tga'),
                               ('BMP', '.bmp'), ('TIFF', '.tif')]:
            with self.subTest(fmt=fmt):
                result = engine.process(self.job(format=fmt, alpha='DISCARD',
                    destination=str(self.root / ('output' + extension))))
                with Image.open(result['output']) as image:
                    image.load()
                    self.assertEqual(image.size, (32, 16))
        result = engine.process(self.job(mipmaps=False))
        data = Path(result['output']).read_bytes()
        self.assertEqual(struct.unpack_from('<I', data, 28)[0], 1)
        self.assertFalse(struct.unpack_from('<I', data, 8)[0] & 0x20000)

    def test_padded_opaque_color_automatically_gets_alpha_capable_dds(self):
        Image.new('RGB', (32, 16), (100, 150, 200)).save(self.source)
        result = engine.process(self.job(resize=True, fit='FIT'))
        self.assertEqual(result['compression'], 'DXT5')
        with Image.open(result['output']) as image:
            self.assertEqual(image.convert('RGBA').getpixel((0, 0))[3], 0)

    def test_data_resizing_does_not_apply_srgb_gamma(self):
        image = Image.new('RGBA', (2, 1))
        image.putdata([(0, 0, 0, 255), (255, 255, 255, 255)])
        data = engine.resize_image(image, (1, 1), 'STRETCH', 'DATA').getpixel((0, 0))[0]
        color = engine.resize_image(image, (1, 1), 'STRETCH', 'COLOR').getpixel((0, 0))[0]
        self.assertTrue(125 <= data <= 130)
        self.assertTrue(185 <= color <= 190)

    def test_worker_reports_failures_per_image_without_false_success(self):
        import json
        request = self.root / 'request.json'
        response = self.root / 'response.json'
        request.write_text(json.dumps([self.job(compression='DXT1'), self.job()]))
        engine.run(request, response)
        rows = json.loads(response.read_text())
        self.assertFalse(rows[0]['ok'])
        self.assertTrue(rows[1]['ok'])


if __name__ == '__main__':
    unittest.main()
