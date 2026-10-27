import pytest
from cryptography.fernet import Fernet

from portal_auto.services.errors import Invalid
from portal_auto.services.vault import CredentialVault


def test_round_trip_hides_plaintext(vault_key):
    vault = CredentialVault(vault_key)
    token = vault.encrypt("s3cret")
    assert "s3cret" not in token
    assert vault.decrypt(token) == "s3cret"


@pytest.mark.parametrize("key", [None, "", "not-a-fernet-key"])
def test_missing_or_invalid_key_rejected(key):
    with pytest.raises(Invalid, match="PORTAL_AUTO_VAULT_KEY"):
        CredentialVault(key)


def test_wrong_key_cannot_decrypt(vault_key):
    token = CredentialVault(vault_key).encrypt("s3cret")
    other = CredentialVault(Fernet.generate_key().decode())
    with pytest.raises(Invalid):
        other.decrypt(token)


def test_corrupt_token_rejected(vault_key):
    with pytest.raises(Invalid):
        CredentialVault(vault_key).decrypt("garbage")
