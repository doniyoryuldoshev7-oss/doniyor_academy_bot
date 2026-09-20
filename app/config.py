from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    bot_token: str
    database_url: str
    admin_ids: str = ""
    web_app_url: str = ""
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
    @property
    def admins(self) -> set[int]:
        return {int(x.strip()) for x in self.admin_ids.split(",") if x.strip()}

settings = Settings()
