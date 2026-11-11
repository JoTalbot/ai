"""Aegis AI Runtime public API."""

from aegis_ai.audit import AuditLog, AuditVerification
from aegis_ai.models import (
    ChatMessage,
    Decision,
    ModelTurn,
    RiskLevel,
    RunResult,
    RunStatus,
    ToolCall,
    ToolSpec,
)
from aegis_ai.policy import SafePolicy
from aegis_ai.provider import OpenAICompatibleProvider
from aegis_ai.registry import ToolRegistrationError, ToolRegistry
from aegis_ai.runtime import AgentRuntime

__version__ = "0.4.0"

__all__ = [
    "AgentRuntime",
    "AuditLog",
    "AuditVerification",
    "ChatMessage",
    "Decision",
    "ModelTurn",
    "OpenAICompatibleProvider",
    "RiskLevel",
    "RunResult",
    "RunStatus",
    "SafePolicy",
    "ToolCall",
    "ToolRegistrationError",
    "ToolRegistry",
    "ToolSpec",
]
