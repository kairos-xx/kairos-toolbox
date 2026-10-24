"""Bowman exceptions."""


class BowmanError(Exception):
    """Base framework error."""


class RouteNotFoundError(BowmanError):
    """No route matched."""


class RequestValidationError(BowmanError):
    """Request data is invalid."""


class AuthenticationError(BowmanError):
    """Authentication failed."""


class AuthorizationError(BowmanError):
    """Authorization failed."""


class SessionError(BowmanError):
    """Session token failed validation."""


class ElevationError(BowmanError):
    """Privilege elevation failed."""
