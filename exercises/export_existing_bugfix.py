"""兼容旧命令；正式入口为 forgepilot.cli.export_fix。"""
from importlib import import_module
import sys

_implementation = import_module("forgepilot.cli.export_fix")
if __name__ == "__main__":
    raise SystemExit(_implementation.main())
else:
    sys.modules[__name__] = _implementation
