"""Arrow exceptions."""


class ArrowError(Exception):
    """Base framework error."""


class RouteNotFoundError(ArrowError):
    """No route matched."""


class RequestValidationError(ArrowError):
    """Request data is invalid."""


class AuthenticationError(ArrowError):
    """Authentication failed."""


class AuthorizationError(ArrowError):
    """Authorization failed."""


class SessionError(ArrowError):
    """Session token failed validation."""


class ElevationError(ArrowError):
    """Privilege elevation failed."""
