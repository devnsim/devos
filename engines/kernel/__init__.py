# ==============================================================================
# FILE: engines/kernel/__init__.py
# OVERVIEW: The engine kernel's public surface: import from here, not from its modules.
# PURPOSE:
#   1. One import line for engine authors and the UI bridge.
# CONTRACT:
#   - Requires: engines.kernel (messages, engine, hosts, bus). Standard library only.
#   - Exposes: Bus, StreamHandle, Subscription, Engine, Job, Reply, InProcessHost, Message, EngineManifest,
#              RequestSpec, EngineError, plain_data, EFFECTS, LOG_LEVELS
# NOTES:
#   - [2026-11-17] Created in Phase 1 of the engine plan.
# ==============================================================================

# ==============================================================================
# [DEFINITIONS]
# None - see engines/kernel/messages.py and docs/architecture/engine-plan.md.
# ==============================================================================

# ==============================================================================
# [INDEX]
# 1. EXPORTS
#    1.1 exports
# ==============================================================================

# ==============================================================================
# [1.1] exports
# ==============================================================================
from engines.kernel.bus import Bus, StreamHandle, Subscription
from engines.kernel.engine import LOG_LEVELS, Engine, Job, Reply
from engines.kernel.hosts import InProcessHost
from engines.kernel.messages import EFFECTS, EngineError, EngineManifest, Message, RequestSpec, plain_data

__all__ = ["Bus", "StreamHandle", "Subscription", "LOG_LEVELS", "Engine", "Job", "Reply", "InProcessHost", "EFFECTS",
           "EngineError", "EngineManifest", "Message", "RequestSpec", "plain_data"]
# --- END [1.1] ---
