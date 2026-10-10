import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import SecretStr

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_path: Path
    llm_base_url: str
    llm_api_key: SecretStr = field(repr=False)
    llm_model: str
    admin_user: str
    admin_password: SecretStr = field(repr=False)
    public_base_url: str


def load_settings(env_file: Path = PROJECT_ROOT / ".env") -> Settings:
    names = (
        "APP_ENV", "DATABASE_PATH", "LLM_BASE_URL", "LLM_API_KEY",
        "LLM_MODEL", "ADMIN_USER", "ADMIN_PASSWORD", "PUBLIC_BASE_URL",
    )
    file_values = dotenv_values(env_file, interpolate=False)
    values = {name: os.environ.get(name, file_values.get(name)) for name in names}
    missing = [name for name, value in values.items() if not value or not value.strip()]
    if missing:
        raise RuntimeError("缺少必要配置：" + ", ".join(missing))
    for name in ("LLM_API_KEY", "LLM_MODEL", "ADMIN_PASSWORD"):
        if values[name] == "REPLACE_ME":
            raise RuntimeError(f"请在本地配置 {name}，不能使用占位值")
    for name in ("LLM_BASE_URL", "PUBLIC_BASE_URL"):
        try:
            url = urlsplit(values[name])
            valid = (
                url.scheme in {"http", "https"} and url.hostname
                and url.port != 0 and not url.username and not url.password
                and not url.query and not url.fragment
                and not any(char.isspace() for char in values[name])
            )
        except ValueError:
            valid = False
        if not valid:
            raise RuntimeError(f"{name} 必须是有效的 HTTP(S) 地址，不得包含凭据、查询或片段") from None
    database_path = Path(values["DATABASE_PATH"])
    if values["DATABASE_PATH"] == ":memory:":
        raise RuntimeError("DATABASE_PATH 必须是持久化文件路径")
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path
    return Settings(
        app_env=values["APP_ENV"], database_path=database_path,
        llm_base_url=values["LLM_BASE_URL"], llm_api_key=SecretStr(values["LLM_API_KEY"]),
        llm_model=values["LLM_MODEL"], admin_user=values["ADMIN_USER"],
        admin_password=SecretStr(values["ADMIN_PASSWORD"]),
        public_base_url=values["PUBLIC_BASE_URL"].rstrip("/"),
    )
