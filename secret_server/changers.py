"""
Password changers: push a newly generated password to the target system
during rotation (Secret Server's "Remote Password Changing").

A changer receives the secret's current decrypted fields and the new password.
It must raise on failure; the vault is only updated after the change succeeds.
"""

from typing import Callable, Dict


class PasswordChangeError(Exception):
    pass


class PasswordChanger:
    name = "base"

    def change(self, fields: Dict[str, str], new_password: str) -> None:
        raise NotImplementedError


class VaultOnlyChanger(PasswordChanger):
    """Stores the new password in the vault only (no remote change)."""
    name = "none"

    def change(self, fields, new_password):
        return None


class ActiveDirectoryChanger(PasswordChanger):
    """
    Changes an Active Directory account password over LDAPS using the account's
    own current credentials. Expects template fields: domain, username, password.
    """
    name = "active-directory"

    def change(self, fields, new_password):
        try:
            from ldap3 import NTLM, Connection, Server
        except ImportError as exc:
            raise PasswordChangeError("ldap3 is required for Active Directory rotation") from exc

        domain, username = fields.get("domain"), fields.get("username")
        if not domain or not username or not fields.get("password"):
            raise PasswordChangeError("secret is missing domain/username/password")
        server = Server(domain, port=636, use_ssl=True)
        conn = Connection(server, user=f"{domain}\\{username}", password=fields["password"],
                          authentication=NTLM, auto_bind=True)
        try:
            base_dn = ",".join(f"DC={part}" for part in domain.split("."))
            conn.search(base_dn, f"(sAMAccountName={_ldap_escape(username)})",
                        attributes=["distinguishedName"])
            if not conn.entries:
                raise PasswordChangeError(f"account {username} not found in {domain}")
            dn = conn.entries[0].entry_dn
            if not conn.extend.microsoft.modify_password(dn, new_password, fields["password"]):
                raise PasswordChangeError(f"AD rejected password change: {conn.result}")
        finally:
            conn.unbind()


def _ldap_escape(value: str) -> str:
    return "".join(f"\\{ord(c):02x}" if c in "\\*()\0" else c for c in value)


CHANGERS: Dict[str, Callable[[], PasswordChanger]] = {
    VaultOnlyChanger.name: VaultOnlyChanger,
    ActiveDirectoryChanger.name: ActiveDirectoryChanger,
}


def register_changer(name: str, factory: Callable[[], PasswordChanger]) -> None:
    CHANGERS[name] = factory


def get_changer(name: str) -> PasswordChanger:
    try:
        return CHANGERS[name]()
    except KeyError:
        raise PasswordChangeError(f"unknown password changer '{name}'")
