"""Fusion 360 add-in command handlers.

Each handler receives (parameters, context) and returns a result dict
with keys: status, result, error (optional).

Handlers are registered individually by FusionBridge._register_all_handlers().
Do NOT auto-import modules here — if any single module fails, it would
block all others from loading.
"""
