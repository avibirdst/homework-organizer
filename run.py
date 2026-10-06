"""应用最简启动脚本。

双击本文件或在终端执行::

    python run.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from homework_organizer.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
