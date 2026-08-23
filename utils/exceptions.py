"""
Domain-specific exceptions for clear error propagation.
"""


class MoodleBotError(Exception):
    """Base class for all application errors."""


class UploadError(MoodleBotError):
    """Raised when uploading a file to Moodle fails."""


class UrlConversionError(MoodleBotError):
    """Raised when the draft URL cannot be converted to a permanent pluginfile URL."""


class AuthenticationError(MoodleBotError):
    """Raised when Moodle login fails."""


class DownloadError(MoodleBotError):
    """Raised when downloading the user-supplied URL fails."""


class FileTooLargeError(MoodleBotError):
    """Raised when the downloaded file exceeds the configured size limit."""
