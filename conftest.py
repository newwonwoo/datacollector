"""Ensure repo root on sys.path for `import collector` during tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
