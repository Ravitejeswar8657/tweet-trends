"""Settings (.env) plus the two YAML files: config.yaml (what to track) and places.yaml (geography)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    x_bearer_token: str = ""
    mock_mode: bool = True
    llm_base_url: str = "https://opencode.ai/zen/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    database_url: str = f"sqlite:///{ROOT / 'data' / 'tracker.db'}"
    config_path: Path = ROOT / "config.yaml"
    places_path: Path = ROOT / "places.yaml"
    disable_scheduler: bool = False  # tests and one-off scripts
    app_username: str = "office"
    app_password: str = ""  # set to require a login (strongly recommended on a public server)


# ---------- places.yaml ----------

class Mandal(BaseModel):
    name: str
    aliases: List[str]
    ambiguous: bool = False


class AssemblyConstituency(BaseModel):
    name: str
    aliases: List[str]
    mandals: List[Mandal]


class NamedArea(BaseModel):
    name: str
    aliases: List[str]


class Places(BaseModel):
    district: NamedArea
    lok_sabha: NamedArea
    assembly_constituencies: List[AssemblyConstituency]

    def ac_names(self) -> List[str]:
        return [ac.name for ac in self.assembly_constituencies]

    def ac(self, name: str) -> Optional[AssemblyConstituency]:
        return next((a for a in self.assembly_constituencies if a.name == name), None)


# ---------- config.yaml ----------

class LocalCfg(BaseModel):
    extra_terms: List[str] = []
    query_suffix: str = "-is:retweet"


class MpCfg(BaseModel):
    handle: str
    track_mentions: bool = True


class PartyCfg(BaseModel):
    label: str
    color: str
    handles: List[str] = []
    keywords: List[str] = []


class Keyword(BaseModel):
    name: str
    query: str


class Location(BaseModel):
    name: str
    woeid: int


class DelhiCfg(BaseModel):
    parliament_handles: List[str] = []
    ap_filter: str = ""
    national_handles: List[str] = []
    state_handles: List[str] = []
    centre_filter: List[str] = []
    topics: List[str] = ["other"]
    keywords: List[Keyword] = []
    trends: List[Location] = []


class Polling(BaseModel):
    local_minutes: int = 60
    party_minutes: int = 60
    burst_minutes: int = 30
    delhi_minutes: int = 60
    trends_minutes: int = 120
    tag_minutes: int = 5
    quiet_hours: str = "23:00-06:00"
    max_backoff_factor: int = 4
    max_results: int = 20
    query_max_length: int = 512


class Budget(BaseModel):
    daily_credit_limit: float = 3.0
    monthly_credit_limit: float = 90.0
    soft_limit_share: float = 0.8
    priority_1: List[str] = ["local", "mp_mentions", "parliament"]
    cost_per_post_read: float = 0.005
    cost_per_user_lookup: float = 0.010
    bill_expanded_users: bool = False
    cost_per_trends_call: float = 0.0
    llm_cost_per_mtok_in: float = 0.0
    llm_cost_per_mtok_out: float = 0.0


class Alerts(BaseModel):
    window_hours: int = 6
    min_negative_posts: int = 5
    spike_factor: float = 2.0
    urgency_threshold: int = 2


class TrackingConfig(BaseModel):
    local: LocalCfg = LocalCfg()
    mp: MpCfg
    parties: Dict[str, PartyCfg]
    delhi: DelhiCfg = DelhiCfg()
    issues: List[str] = Field(default_factory=lambda: ["other"])
    polling: Polling = Polling()
    budget: Budget = Budget()
    alerts: Alerts = Alerts()


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_config() -> TrackingConfig:
    with open(get_settings().config_path, encoding="utf-8") as f:
        return TrackingConfig(**yaml.safe_load(f))


@lru_cache
def get_places() -> Places:
    with open(get_settings().places_path, encoding="utf-8") as f:
        return Places(**yaml.safe_load(f))
