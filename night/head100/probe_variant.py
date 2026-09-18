# -*- coding: utf-8 -*-
"""Free hypothesis check: same instrument, a variant target file (argv[1]) -> argv[2]."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from night import sectprobe
sectprobe.ADJ = (Path(sys.argv[1]).resolve(),)
sectprobe.run(Path(sys.argv[2]))
