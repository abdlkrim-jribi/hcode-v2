from dataclasses import dataclass

@dataclass
class Config:
    model: str
    api_key: str
    base_url: str