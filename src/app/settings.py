"""User settings: one JSON file, `~/.reels/settings.json` (the folder moves with the REELS_HOME environment variable).

Reels has no settings of its own yet; the template's sample ones (a greeting word and style) went with the sample. The
declaration stays because it names the app's home, where the settings file and the fallback database live, and the
`REELS_*` variables. The file handling, precedence, validation and the `GET`/`PUT /api/settings` routes are
[strata_python.settings](https://github.com/jburnhams/strata-python); this module only declares the app's settings."""

from strata_python.settings import Settings

# not the template's "app": its home and APP_* variables would be shared with every other app made from the template
SETTINGS = Settings("reels", [])
