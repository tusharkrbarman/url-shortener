import hashlib
import hmac

from .errors import Unauthorized


class Authenticator:
    def __init__(self, api_keys: tuple[str, ...]):
        self.api_keys = api_keys

    def authenticate(self, authorization_header: str | None) -> str:
        if not authorization_header or not authorization_header.startswith("Bearer "):
            raise Unauthorized()
        token = authorization_header.removeprefix("Bearer ").strip()
        for configured in self.api_keys:
            if hmac.compare_digest(token, configured):
                return self.owner_id_for_token(token)
        raise Unauthorized("Invalid authentication token.", code="invalid_token")

    @staticmethod
    def owner_id_for_token(token: str) -> str:
        return "owner_" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]

