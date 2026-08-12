"""Backend for the chat application.

Today: the session gate and the scoped data-access layer it protects.
Later: `api/` for the FastAPI layer. Nothing here imports a web framework,
so the security boundary stays testable without one.
"""
