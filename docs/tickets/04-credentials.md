# 04 Credentials + Vault
Do: `CredentialVault` (Fernet, key from env, fail fast), `credential` table, `CredentialService`, Credentials screen under a Portal (password masked, blank password on edit = keep).
Accept: DB never holds plaintext (test inspects the column); decrypt round-trip; wrong key errors clearly; unique label per portal.
