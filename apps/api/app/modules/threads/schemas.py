"""Request and response bodies for conversations."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

MessageRole = Literal["user", "assistant", "system", "tool"]
MessageStatus = Literal["pending", "streaming", "complete", "failed", "cancelled"]
SearchScope = Literal["threads", "messages"]

Title = Annotated[str, Field(min_length=1, max_length=200)]

# 32k characters is roughly a 40-page document pasted into the composer. Longer input
# belongs in a file attachment, which is chunked and retrieved rather than sent whole.
MessageContent = Annotated[str, Field(min_length=1, max_length=32_000)]


class ThreadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    title_generated: bool
    pinned: bool
    archived: bool
    message_count: int
    last_message_at: dt.datetime | None
    created_at: dt.datetime


class ThreadCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title | None = None


class ThreadUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title | None = None
    pinned: bool | None = None
    archived: bool | None = None


class ThreadListResponse(BaseModel):
    items: list[ThreadResponse]
    next_cursor: str | None = None


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    thread_id: uuid.UUID
    seq: int
    role: MessageRole
    content: str
    status: MessageStatus
    model: str | None = None
    finish_reason: str | None = None
    created_at: dt.datetime


class MessageListResponse(BaseModel):
    items: list[MessageResponse]
    next_cursor: str | None = None


class MessageOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice: bool = False
    stream: bool = True
    language: str | None = Field(default=None, max_length=8)


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: MessageContent
    # Client-generated and unique per user, so a double-tap on Send resolves to the same
    # turn instead of billing twice.
    client_message_id: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    options: MessageOptions = Field(default_factory=MessageOptions)


class MessageAcceptedResponse(BaseModel):
    """The turn is persisted before the model is called.

    If generation dies mid-flight the assistant row still exists with `status='failed'`
    and can be regenerated — instead of the turn vanishing, which is what the legacy
    client did on every error.
    """

    message_id: uuid.UUID
    assistant_message_id: uuid.UUID
    seq: int
    stream_url: str
    idempotent_replay: bool = False


class SummaryResponse(BaseModel):
    """What the model will be told about the earlier part of the conversation.

    Exposed because a compressed record of someone's own conversation is something they
    are entitled to read, and to disagree with.
    """

    summary: str | None
    summary_upto_seq: int
    summarised: bool


class SearchHit(BaseModel):
    thread_id: uuid.UUID
    thread_title: str
    message_id: uuid.UUID | None = None
    seq: int | None = None
    role: MessageRole | None = None
    snippet: str
    created_at: dt.datetime


class SearchResponse(BaseModel):
    scope: SearchScope
    items: list[SearchHit]
    next_cursor: str | None = None
