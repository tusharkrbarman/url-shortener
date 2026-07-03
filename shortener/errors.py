class AppError(Exception):
    status_code = 500
    code = "internal_error"
    message = "An unexpected error occurred."

    def __init__(self, message=None, *, code=None, status_code=None):
        super().__init__(message or self.message)
        self.message = message or self.message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code


class BadRequest(AppError):
    status_code = 400
    code = "bad_request"
    message = "The request is invalid."


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"
    message = "Authentication is required."


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"
    message = "You do not have access to this resource."


class NotFound(AppError):
    status_code = 404
    code = "not_found"
    message = "The requested resource was not found."


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with current state."


class Gone(AppError):
    status_code = 410
    code = "gone"
    message = "The resource is no longer available."


class RateLimited(AppError):
    status_code = 429
    code = "rate_limit_exceeded"
    message = "Too many requests. Please retry later."


class DependencyUnavailable(AppError):
    status_code = 503
    code = "dependency_unavailable"
    message = "A required dependency is unavailable."

