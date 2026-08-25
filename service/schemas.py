from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
import re


class AnalyzeRequest(BaseModel):
    filter_url: str
    prompt_preset: Optional[str] = None        # "vehicles" | "realestate" | None
    custom_ad_prompt: Optional[str] = None
    custom_summary_prompt: Optional[str] = None

    @field_validator("filter_url")
    @classmethod
    def valid_olx_url(cls, v: str) -> str:
        v = v.strip()
        allowed = ("olx.pl", "otomoto.pl", "otodom.pl")
        if not any(h in v for h in allowed):
            raise ValueError("URL must be from olx.pl, otomoto.pl, or otodom.pl")
        if not v.startswith("http"):
            raise ValueError("URL must start with http:// or https://")
        return v

    @field_validator("prompt_preset")
    @classmethod
    def valid_preset(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("vehicles", "realestate"):
            raise ValueError("preset must be 'vehicles' or 'realestate'")
        return v


class ReportStatus(BaseModel):
    id: str
    status: str
    ads_found: Optional[int]
    ads_analyzed: Optional[int]
    is_limited: bool
    has_preset: bool


class MagicLinkRequest(BaseModel):
    email: EmailStr


class CheckoutRequest(BaseModel):
    plan: str = "monthly"

    @field_validator("plan")
    @classmethod
    def valid_plan(cls, v: str) -> str:
        if v not in ("monthly", "weekly", "daily"):
            raise ValueError("plan must be monthly, weekly, or daily")
        return v
