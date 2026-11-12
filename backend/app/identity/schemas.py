from uuid import UUID

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)
    handle: str = Field(max_length=30)
    name: str = Field(max_length=80)


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class ChannelSummary(BaseModel):
    handle: str
    name: str
    description: str
    avatar_url: str | None
    subscriber_count: int
    viewer_subscribed: bool


class QuotaSummary(BaseModel):
    bytes_committed: int
    bytes_reserved: int
    bytes_limit: int


class UserSummary(BaseModel):
    id: UUID
    email: str
    role: str
    channel: ChannelSummary


class SessionResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: UserSummary


class MeResponse(BaseModel):
    id: UUID
    email: str
    role: str
    channel: ChannelSummary
    quota: QuotaSummary


class ChannelUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    description: str | None = Field(default=None, max_length=2000)


class ErrorDetail(BaseModel):
    field: str
    issue: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = []
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
