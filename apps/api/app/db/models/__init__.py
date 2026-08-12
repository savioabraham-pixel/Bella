"""Model registry.

Alembic autogenerate only sees what is imported here, so every model must be re-exported.
"""

from app.db.base import Base
from app.db.models.conversations import (
    Message,
    MessageAttachment,
    MessageAudio,
    Thread,
)
from app.db.models.files import File, FileChunk
from app.db.models.identity import (
    Profile,
    Relationship,
    Session,
    User,
    UserPreferences,
    UserQuota,
)
from app.db.models.memory import EMBEDDING_DIMENSIONS, Memory
from app.db.models.system import (
    AuditLog,
    Consent,
    DataRequest,
    FeatureFlag,
    PromptVersion,
    ToolInvocation,
)

__all__ = [
    "EMBEDDING_DIMENSIONS",
    "AuditLog",
    "Base",
    "Consent",
    "DataRequest",
    "FeatureFlag",
    "File",
    "FileChunk",
    "Memory",
    "Message",
    "MessageAttachment",
    "MessageAudio",
    "Profile",
    "PromptVersion",
    "Relationship",
    "Session",
    "Thread",
    "ToolInvocation",
    "User",
    "UserPreferences",
    "UserQuota",
]

# Tables that carry user-owned data and therefore require an RLS policy. The security
# test suite asserts this list matches what the database actually has enabled.
RLS_TABLES = (
    "profiles",
    "relationships",
    "sessions",
    "user_preferences",
    "user_quotas",
    "threads",
    "messages",
    "message_audio",
    "memories",
    "files",
    "file_chunks",
    "tool_invocations",
    "consents",
    "data_requests",
)
