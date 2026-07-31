"""
services/incremental_sync/ — standalone incremental sync module.

NOT wired into any route yet. normal_sync.py remains the live production
sync path untouched. See this package's README.md for the swap-over plan
once this has been validated against a real account.
"""

from services.incremental_sync.orchestrator import sync_user_incremental

__all__ = ["sync_user_incremental"]
