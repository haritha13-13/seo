from cryptography.fernet import Fernet, InvalidToken

from portal_auto.services.errors import Invalid


class CredentialVault:
    def __init__(self, key: str | None):
        try:
            self._fernet = Fernet(key.encode())
        except (AttributeError, ValueError):
            raise Invalid("PORTAL_AUTO_VAULT_KEY is not set or invalid") from None

    def encrypt(self, plain: str) -> str:
        return self._fernet.encrypt(plain.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            raise Invalid("Stored password cannot be decrypted with the current vault key") from None
