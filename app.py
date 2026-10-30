"""Compatibility entry point: python3 -m uvicorn app:app --reload."""
from bobaops.main import app

__all__ = ['app']
