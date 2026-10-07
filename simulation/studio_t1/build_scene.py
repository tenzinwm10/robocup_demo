"""Build a separate Studio 3v3 T1 scene from its installed, unmodified T1 asset."""
import argparse
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET
import zipfile
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent


def pitch_texture(field):
    hx, hy = field['length']/2 + 2, field['width']/2 + 1.5
    w, h = 2048, round(2048*hy/hx)
    image = Image.new('RGB', (w, h), '#267438')
    draw = ImageDraw.Draw(image)
    def point(x, y):
        return ((x+hx)*w/(2*hx), (hy-y)*h/(2*hy))
    thick = max(2, round(0.05*w/(2*hx)))
    for i in range(12):
        if i % 2 == 0:
            x0, x1 = -hx + 2*hx*i/12, -hx + 2*hx*(i+1)/12
            draw.rectangle([point(x0, hy), point(x1, -hy)], fill='#2c803e')
    def line(x1, y1, x2, y2):
        draw.line([point(x1, y1), point(x2, y2)], fill='white', width=thick)
    L, W = field['length']/2, field['width']/2
    for x in (-L, L):
        line(x, -W, x, W)
    for y in (-W, W):
        line(-L, y, L, y)
    line(0, -W, 0, W)
    radius = field['circle_radius']
    draw.ellipse([point(-radius, radius), point(radius, -radius)], outline='white', width=thick)
    for side in (-1, 1):
        for key in ('penalty_area', 'goal_area'):
            x = side*(L-field[key+'_length'])
            y = field[key+'_width']/2
            line(side*L, y, x, y)
            line(x, y, x, -y)
            line(x, -y, side*L, -y)
        x = side*(L-field['penalty_dist'])
        line(x-0.12, 0, x+0.12, 0)
        line(x, -0.12, x, 0.12)
    line(-0.12, 0, 0.12, 0)
    line(0, -0.12, 0, 0.12)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def build(library, output):
    field = json.loads((HERE/'field.json').read_text())
    source = Path(library)/'scenes'/'football_pitch_T1.bscene'
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(Path(library)/'assets'/'robot_t1.basset') as stock_robot:
        robot_files = {name: stock_robot.read(name) for name in stock_robot.namelist()}
    robot_manifest = json.loads(robot_files['manifest.json'])
    robot_manifest.update(id='robot_t1_fcisar', name='T1 FC Isar Sensors', origin='user',
                          deletable=True, runtime_extensions=[],
                          builtin_id='fcisar-asset-robot-t1-sensors', builtin_revision='1.0.0',
                          description='Unmodified T1 body; sensor extensions belong to the competition scene')
    robot_files['manifest.json'] = json.dumps(robot_manifest, indent=2).encode()
    robot_path = output.with_name('robot_t1_fcisar.basset')
    with zipfile.ZipFile(robot_path, 'w', zipfile.ZIP_DEFLATED) as stock_robot:
        for name, content in robot_files.items():
            stock_robot.writestr(name, content)
    with zipfile.ZipFile(source) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        with zipfile.ZipFile(io.BytesIO(archive.read('model.mjspec.zip'))) as model_archive:
            model_files = {name: model_archive.read(name) for name in model_archive.namelist()}
        thumbnail = archive.read('thumbnail.webp')
    xml = ET.fromstring(model_files['MuJoCo Model.xml'])
    xml.set('model', 'FC Isar T1 RoboLeague 3v3')
    L, W = field['length']/2, field['width']/2
    for geom in xml.findall('./worldbody/geom'):
        name = geom.get('name', '')
        if name == 'pitch':
            geom.set('size', f'{L+2} {W+1.5} 0.1')
        elif name.startswith('board_'):
            size = [float(v) for v in geom.get('size').split()]
            pos = [float(v) for v in geom.get('pos').split()]
            long = 'front' in name or 'back' in name
            size[0] = L+2 if long else W+1.5
            axis = 1 if long else 0
            pos[axis] = math.copysign((W+1.5 if long else L+2) + (0.01 if 'outside' in name else 0), pos[axis])
            geom.set('size', ' '.join(map(str, size)))
            geom.set('pos', ' '.join(map(str, pos)))
    for goal, x in [('goal-left', -L), ('goal-right', L)]:
        body = xml.find(f'./worldbody/body[@name="{goal}"]')
        body.set('pos', f'{x} 0 0')
    world = xml.find('worldbody')
    for frame in list(world.findall('frame')):
        if frame.get('name', '').startswith('__booster_robot_frame_'):
            world.remove(frame)
    template = copy.deepcopy(manifest['robot_instances'][0])
    template['asset_key'] = 'robot_t1_fcisar'
    robots = []
    for index in range(6):
        home = index < 3
        robot = copy.deepcopy(template)
        name = f'robot{index+1}'
        robot.update(name=name, root_body=f'{name}_Trunk',
                     pos=[-8.0 if home else 8.0, (index%3-1)*2.5, 0.0],
                     quat=[1.0, 0.0, 0.0, 0.0] if home else [0.0, 0.0, 0.0, 1.0],
                     color=[1.0, 0.3451, 0.3922, 1.0] if home else [0.2431, 0.6235, 1.0, 1.0])
        robots.append(robot)
        ET.SubElement(world, 'frame', name=f'__booster_robot_frame_{name}')
    model_files['MuJoCo Model.xml'] = ET.tostring(xml, encoding='utf-8', xml_declaration=True)
    model_files['3v3_ori.png'] = pitch_texture(field)
    model_buffer = io.BytesIO()
    with zipfile.ZipFile(model_buffer, 'w', zipfile.ZIP_DEFLATED) as nested:
        for name, content in model_files.items():
            nested.writestr(name, content)
    manifest.update(id='fcisar_t1_robo_league_3v3', name='FC Isar T1 RoboLeague 3v3',
                    description='T2 brain, six T1 bodies, 22.003 x 14.126 m',
                    origin='user', deletable=True, robot_instances=robots,
                    runtime_profile='standard', is_default=False,
                    scene_policy={'entry': 'selectable', 'switch_away': True, 'edit': True})
    manifest['dependencies'] = [{'kind': 'asset', 'asset_type': 'robot',
        'preferred_key': 'robot_t1_fcisar', 'robot_model': 'T1', 'required': True,
        'builtin_id': robot_manifest['builtin_id'], 'builtin_revision': robot_manifest['builtin_revision'],
        'sha256': hashlib.sha256(robot_path.read_bytes()).hexdigest()}]
    for key in ('builtin_id', 'builtin_revision', 'display_order'):
        manifest.pop(key, None)
    detection_params = {'field_type': 'custom', 'detection_fps': '10', 'worker_count': '1',
                        'yolo_export_enabled': 'false'}
    detection_params.update({'field_'+key: str(value) for key, value in field.items()})
    referee_params = {'publish_transport': 'json_string', 'json_topic': '/soccer/game_controller',
                      'players_per_team': '3', 'home_team_number': '1', 'away_team_number': '2',
                      'home_robots': 'robot1:1,robot2:2,robot3:3',
                      'away_robots': 'robot4:1,robot5:2,robot6:3',
                      'initial_home_team_name': 'FC Isar Red', 'initial_away_team_name': 'FC Isar Blue',
                      'field_orientation': 'homeDefendsLeftGoal', 'ground_truth_enabled': 'true',
                      'home_ground_truth_mirror': 'false', 'away_ground_truth_mirror': 'true'}
    referee_params.update({'field_type': 'custom', **{'field_'+k: str(v) for k, v in field.items()}})
    extensions = [
        {'point': 'visual_scene', 'name': 'game_control', 'params': {'mode': 'forced'}},
        {'point': 'extension_process', 'name': 'game_control', 'params': referee_params},
        {'point': 'extension_process', 'name': 'detection_extension', 'params': detection_params},
        {'point': 'extension_process', 'name': 'pose_publisher_extension', 'params': {}},
        {'point': 'extension_process', 'name': 'rgb_publisher_extension',
         'params': {'enable_compression': 'false', 'jpeg_quality': '75'}},
        {'point': 'extension_process', 'name': 'depth_publisher_extension', 'params': {}},
    ]
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('manifest.json', json.dumps(manifest, indent=2))
        archive.writestr('extensions.json', json.dumps(extensions, indent=2))
        archive.writestr('field.json', json.dumps(field, indent=2))
        archive.writestr('model.mjspec.zip', model_buffer.getvalue())
        archive.writestr('thumbnail.webp', thumbnail)
    print(json.dumps({'scene': str(output), 'robots': len(robots), 'field': field,
                      'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', required=True)
    parser.add_argument('--output', default=str(HERE/'scenes'/'fcisar_t1_robo_league_3v3.bscene'))
    args = parser.parse_args()
    build(args.library, args.output)
