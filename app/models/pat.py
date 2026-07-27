from datetime import datetime
from typing import Annotated
from beanie import Document, Indexed, PydanticObjectId
from pydantic import Field

class PersonalAccessToken(Document):
    user_id: PydanticObjectId = Field(description="Reference to the owner user")
    name: str = Field(description="Name of the token (e.g. Claude Connector)")
    token_hash: Annotated[str, Indexed(unique=True)] = Field(description="SHA-256 hash of the PAT token")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    is_active: bool = True

    class Settings:
        name = "personal_access_tokens"
        indexes = [
            "user_id",
            "token_hash"
        ]

    def __repr__(self) -> str:
        return f"<PersonalAccessToken {self.name} active={self.is_active}>"
