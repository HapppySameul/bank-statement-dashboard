import sys
from pathlib import Path

# The app modules live in src/; make them importable from the tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
