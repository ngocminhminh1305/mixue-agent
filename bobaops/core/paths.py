"""Workspace paths anchored to this package, never the shell working directory."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT / 'frontend'
PRIVATE_DATA_DIR = ROOT / 'data' / 'private'
