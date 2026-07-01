"""FastAPI backend for the local control-plane dashboard.

Serves the SvelteKit frontend (Phase 9+), exposes the pipeline state via
HTTP endpoints, and drives the existing CLI scripts as subprocesses.
Runs side-by-side with the Telegram bot — both write to the same
SQLite state, both are valid approve/reject surfaces.
"""
