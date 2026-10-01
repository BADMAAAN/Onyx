from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any


@dataclass(slots=True)
class Config:
    root: Path
    values: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> "Config":
        path = path.resolve()
        with path.open("rb") as stream:
            values = tomllib.load(stream)
        from kk.core.desktop_settings import DesktopSettings
        settings=DesktopSettings(path.parent)
        for key,dotted in {'microphone':('voice','microphone'),'output_device':('voice','output_device'),
                           'show_hotkey':('desktop','show_hotkey'), 'wake_enabled':('wakeword','enabled'),
                           'observation_paused':('context','paused'),'excluded_apps':('context','excluded_apps')}.items():
            value=settings.get(key)
            if value is not None:values.setdefault(dotted[0],{})[dotted[1]]=value
        if settings.get('retention_days') is not None:values.setdefault('context',{})['persistent_retention_seconds']=settings.get('retention_days')*86400
        return cls(path.parent, values)

    def get(self, dotted: str, default: Any = None) -> Any:
        value: Any = self.values
        for part in dotted.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return value

