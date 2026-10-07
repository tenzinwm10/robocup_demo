"""Validate the portable scene's geometry, roster, sensors and stock body payload."""
import hashlib
import io
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
import zipfile

HERE = Path(__file__).resolve().parent

class ScenePackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with zipfile.ZipFile(HERE/'scenes/fcisar_t1_robo_league_3v3.bscene') as scene:
            cls.manifest = json.loads(scene.read('manifest.json'))
            cls.extensions = json.loads(scene.read('extensions.json'))
            cls.field = json.loads(scene.read('field.json'))
            with zipfile.ZipFile(io.BytesIO(scene.read('model.mjspec.zip'))) as model:
                cls.xml = ET.fromstring(model.read('MuJoCo Model.xml'))

    def test_competition_dimensions_agree(self):
        self.assertEqual(self.field, json.loads((HERE/'field.json').read_text()))
        self.assertEqual((self.field['length'], self.field['width']), (22.003, 14.126))
        for name, x in (('goal-left', -11.0015), ('goal-right', 11.0015)):
            pos = self.xml.find(f'./worldbody/body[@name="{name}"]').get('pos').split()
            self.assertAlmostEqual(float(pos[0]), x)
        for extension in self.extensions:
            if extension['name'] in ('game_control', 'detection_extension') and extension['point'] == 'extension_process':
                self.assertEqual(extension['params']['field_type'], 'custom')
                for key, value in self.field.items():
                    self.assertAlmostEqual(float(extension['params']['field_'+key]), value)

    def test_six_stock_t1_instances(self):
        robots = self.manifest['robot_instances']
        self.assertEqual([robot['name'] for robot in robots], [f'robot{i}' for i in range(1, 7)])
        self.assertTrue(all(robot['asset_key'] == 'robot_t1_fcisar' for robot in robots))
        self.assertTrue(all(abs(robot['pos'][0]) < self.field['length']/2 for robot in robots))
        dependency = self.manifest['dependencies'][0]
        self.assertEqual(dependency['sha256'], hashlib.sha256((HERE/'scenes/robot_t1_fcisar.basset').read_bytes()).hexdigest())

    def test_sensor_extensions_and_roster(self):
        names = {extension['name'] for extension in self.extensions}
        self.assertTrue({'detection_extension', 'pose_publisher_extension', 'rgb_publisher_extension', 'depth_publisher_extension'} <= names)
        referee = next(extension['params'] for extension in self.extensions
                       if extension['name'] == 'game_control' and extension['point'] == 'extension_process')
        self.assertEqual(referee['home_robots'], 'robot1:1,robot2:2,robot3:3')
        self.assertEqual(referee['away_robots'], 'robot4:1,robot5:2,robot6:3')
        self.assertEqual(referee['publish_transport'], 'json_string')

if __name__ == '__main__': unittest.main(verbosity=2)
