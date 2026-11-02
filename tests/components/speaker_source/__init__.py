"""Build the production player on the host for its unit tests."""

import esphome.config_validation as cv
from esphome.types import ConfigType
from tests.testing_helpers import ComponentManifestOverride


def override_manifest(manifest: ComponentManifestOverride) -> None:
    manifest.dependencies = ["media_player", "media_source", "speaker"]
    manifest.config_schema = cv.Schema({})

    async def to_code_testing(config: ConfigType) -> None:
        """The tests configure the player themselves."""

    manifest.to_code = to_code_testing
