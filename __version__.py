"""Single source of truth for the Wheel of Choice version number.

The release workflow (.github/workflows/release.yml) rewrites this on every
published GitHub release — it takes the release tag, strips a leading "v",
writes it here, and commits back to main — so the container image built for
that release carries the matching number. The in-app version display and the
"update available" check both read from here.
"""

__version__ = "3.0.0"
