from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from typing import Tuple, Type


class Settings(BaseSettings):
    openai_api_key: str
    elevenlabs_api_key: str = ""
    youtube_api_key: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # default Rachel voice

    # When false: only result_*.json + meta are written under backend/storage.
    # Upload HTML/PDF are never persisted there (they stay in a temp dir until parse ends).
    # Set AUTOCON_SAVE_DOCX=true to also generate and store the annotated Word file.
    save_docx_artifact: bool = Field(
        default=False,
        validation_alias=AliasChoices("AUTOCON_SAVE_DOCX"),
    )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        # .env file takes priority over system environment variables
        return init_settings, dotenv_settings, env_settings, file_secret_settings


settings = Settings()
