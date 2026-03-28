#!/usr/bin/env python3

import sys
from pathlib import Path
import runpy


if __name__ == "__main__":
    scripts_dir = Path(__file__).resolve().parent / "scripts"
    script_path = scripts_dir / "run_eval_locomo.py"
    sys.path.insert(0, str(scripts_dir))
    runpy.run_path(str(script_path), run_name="__main__")
