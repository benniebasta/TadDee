"""TadDee: nesting, CNC/laser export and 3D relief for the factory.

Settings come from TADDEE_* environment variables (TADDEE_PORT, TADDEE_DATA_DIR,
TADDEE_BACKUP_DIR, TADDEE_HOST). The older NEST_* names still work; when both
are set, TADDEE_* wins.
"""
import os as _os

APP_NAME = "TadDee"

for _k, _v in list(_os.environ.items()):
    if _k.startswith("TADDEE_"):
        _os.environ["NEST_" + _k[len("TADDEE_"):]] = _v
