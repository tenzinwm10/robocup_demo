"""Add custom-field configuration to Studio's referee; motion code is untouched.

Run inside the Studio virtual-robot container before selecting the custom scene.
Both substitutions are guarded and original files are retained beside the source.
"""
import ast
from pathlib import Path

ROOT = Path('/usr/local/booster_robot/booster_robocup_sim/extensions/game_control')
INSERT = '''        # FCISAR_CUSTOM_FIELD: isolated to scenes with explicit custom dimensions.
        if self._context.get_param("field_type", "") == "custom":
            import json
            keys = ("length", "width", "penalty_dist", "goal_width", "circle_radius",
                    "penalty_area_length", "penalty_area_width", "goal_area_length",
                    "goal_area_width", "goal_height", "goal_depth")
            dimensions = {key: float(self._context.get_param("field_" + key, 0)) for key in keys}
            if not all(value > 0 for value in dimensions.values()):
                raise ValueError("Custom referee field dimensions must be positive")
            os.environ["FCISAR_CUSTOM_FIELD_JSON"] = json.dumps(dimensions)
        else:
            os.environ.pop("FCISAR_CUSTOM_FIELD_JSON", None)
'''


def patch(path, original, replacement):
    text = path.read_text()
    if 'FCISAR_CUSTOM_FIELD' in text:
        print(f'Already configured: {path.name}')
        return
    if text.count(original) != 1:
        raise RuntimeError(f'Unsupported Studio runtime source: {path}')
    changed = text.replace(original, replacement)
    ast.parse(changed)
    backup = path.with_suffix(path.suffix + '.fcisar-original')
    if not backup.exists():
        backup.write_text(text)
    path.write_text(changed)
    print(f'Configured: {path.name}; backup={backup}')


patch(ROOT/'extension.py', '        config = config_from_context(self._context)\n',
      '        config = config_from_context(self._context)\n' + INSERT)
patch(ROOT/'rules.py', '        self.field = field or FieldDimensions()\n',
      '        # FCISAR_CUSTOM_FIELD: construct cached rule geometry consistently.\n'
      '        import json, os\n'
      '        self.field = field or FieldDimensions(**json.loads(os.environ.get("FCISAR_CUSTOM_FIELD_JSON", "{}")))\n')

anchor = "        class_path='extensions.game_control.extension:GameControlExtension',\n        parameters=[\n"
fields = ("length", "width", "penalty_dist", "goal_width", "circle_radius", "penalty_area_length",
          "penalty_area_width", "goal_area_length", "goal_area_width", "goal_height", "goal_depth")
schema = "            # FCISAR_CUSTOM_FIELD: allow explicitly configured competition geometry.\n"
schema += "            ParameterSpec(name='field_type', type=str, default='adult_size', required=False),\n"
schema += ''.join(f"            ParameterSpec(name='field_{key}', type=float, default=None, required=False),\n" for key in fields)
patch(ROOT.parent.parent/'core'/'builtin_extensions.py', anchor, anchor + schema)
