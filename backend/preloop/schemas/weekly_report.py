"""Report email controls, independent from approval notifications."""

from pydantic import BaseModel, ConfigDict


class WeeklyReportPreferences(BaseModel):
    """Effective account and personal delivery switches."""

    account_enabled: bool
    user_enabled: bool


class WeeklyReportPreferencesUpdate(BaseModel):
    """Omitted fields preserve the current setting."""

    model_config = ConfigDict(extra="forbid")
    account_enabled: bool | None = None
    user_enabled: bool | None = None
