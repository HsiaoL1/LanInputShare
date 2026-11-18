from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppPaths:
    root: Path
    config_file: Path
    log_dir: Path
    identity_dir: Path
    staging_dir: Path

    @classmethod
    def from_environment(cls) -> AppPaths:
        if sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support" / "LanInputShare"
        elif sys.platform == "win32":
            local_app_data = os.environ.get("LOCALAPPDATA")
            if not local_app_data:
                raise RuntimeError("LOCALAPPDATA 未设置")
            root = Path(local_app_data) / "LanInputShare"
        else:
            raise RuntimeError("目前仅支持 Windows 和 macOS")
        return cls(
            root=root,
            config_file=root / "config.json",
            log_dir=root / "logs",
            identity_dir=root / "identity",
            staging_dir=root / "staging",
        )
