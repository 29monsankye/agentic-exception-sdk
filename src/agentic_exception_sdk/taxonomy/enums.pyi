from enum import Enum, StrEnum

__all__ = ['AgentExceptionClass', 'EscalationLevel', 'ExceptionSource']

class AgentExceptionClass(StrEnum):
    EXCEPTION = 'exception'
    ISSUE = 'issue'
    HARD_KILL = 'hard_kill'

class ExceptionSource(StrEnum):
    MODEL = 'model'
    TOOL = 'tool'
    ORCHESTRATION = 'orchestration'
    PLANNING = 'planning'
    DATA_ENV = 'data_env'

class EscalationLevel(int, Enum):
    L0_SELF_RETRY = 0
    L1_FALLBACK_PATH = 1
    L2_CHECKPOINT_HANDOFF = 2
    L3_HUMAN_ESCALATION = 3
    L4_SAFE_ABORT = 4
