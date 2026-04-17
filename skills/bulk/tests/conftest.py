"""Put the package dir on sys.path so `import chop_bulk` works without install.

Mirrors the pattern from skills/up-to-date/conftest.py.
"""

import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))
