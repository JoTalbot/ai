"""Deterministic default policy for tool risk classes."""

from aegis_ai.models import Decision, RiskLevel, ToolSpec


class SafePolicy:
    """Allow reads, require host approval for side effects, deny destructive actions."""

    def decide(self, tool: ToolSpec) -> Decision:
        if tool.risk is RiskLevel.READ_ONLY:
            return Decision.ALLOW
        if tool.risk is RiskLevel.DESTRUCTIVE:
            return Decision.DENY
        return Decision.REQUIRE_APPROVAL
