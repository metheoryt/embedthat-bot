from pydantic import RedisDsn, Field, AliasChoices
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    bot_token: str
    redis_dsn: RedisDsn = Field(
        default="redis://redis", validation_alias=AliasChoices("redis_url")
    )
    loglevel: str = "INFO"

    dump_chat_id: int  # where parts of YouTube videos will be posted to be sent as a media group later
    feed_channel_id: int | None = None
    admin_chat_id: int | None = None

    # populated on setup
    bot_username: str | None = None


# noinspection PyArgumentList
settings = Settings()
