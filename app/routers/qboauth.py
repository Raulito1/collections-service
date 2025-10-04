# app/routers/qboauth.py
"""Compatibility shim for legacy imports.

This module simply re-exports the QuickBooks router so existing code that
imports `app.routers.qboauth` continues to work.
"""

from .quickbooks import router  # re-export for app.main

__all__ = ["router"]
