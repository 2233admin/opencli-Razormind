from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Platform = str


class BrowserAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    platform: str | None = Field(default=None, min_length=1, max_length=255)
    site_url: str | None = Field(default=None, min_length=1, max_length=2048)
    label: str | None = Field(default=None, min_length=1, max_length=100)
    # Omission provisions a new persistent Docker browser; never picks a random slot.
    browser_instance_id: str | None = Field(default=None, min_length=1, max_length=36)

    @model_validator(mode="after")
    def website_required(self):
        if not self.site_url and not self.platform:
            raise ValueError("请输入网站地址")
        return self


class BrowserLoginAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["click", "text", "key", "scroll", "reload"]
    x: int = Field(default=0, ge=0, le=10000)
    y: int = Field(default=0, ge=0, le=10000)
    text: str = Field(default="", max_length=2000)
    key: Literal[
        "Enter",
        "Backspace",
        "Tab",
        "Escape",
        "Delete",
        "ArrowLeft",
        "ArrowRight",
        "ArrowUp",
        "ArrowDown",
        "Home",
        "End",
    ] = "Enter"
    delta: int = Field(default=0, ge=-1000, le=1000)


class BrowserAccountDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clear_login_data: bool


class BrowserAccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=100)


class BrowserAccountConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["confirmed", "needs_login"]
