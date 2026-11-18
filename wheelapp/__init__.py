"""The Wheel of Choice server, one module per area. Importing the package
registers every route on the shared Flask app (core.app)."""
from . import core, i18n, updates, storage, accounts, oidc, push, calendars, maps, seeding, wheel_api, admin_api, backup, validation, rounds, entries, polls, site  # noqa: F401 — registers the routes

app = core.app
