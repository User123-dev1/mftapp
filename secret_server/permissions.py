"""
Permission model (mirrors Secret Server's folder / secret ACLs).

Levels: View < Edit < Owner.

* A folder either inherits permissions from its parent or defines its own.
* A secret either inherits permissions from its folder or defines its own.
* Effective level = highest level granted to the user directly or to any of
  their groups. Administrators are implicitly Owner everywhere.
* Secrets that require approval give View-only users access only while they
  hold an approved, unexpired access request (they can still see the secret's
  metadata and file a request). Editors, Owners and View-level members of the
  secret's approver group bypass the workflow.
"""

from typing import Optional

from .auth import Principal
from .db import EDIT, OWNER, VIEW, now_iso


def _level_from_acl(conn, table: str, key_column: str, key: int, principal: Principal) -> int:
    best = 0
    for row in conn.execute(
        f"SELECT principal_type, principal_id, level FROM {table} WHERE {key_column} = ?",
        (key,),
    ):
        if (row["principal_type"] == "user" and row["principal_id"] == principal.user_id) or (
            row["principal_type"] == "group" and row["principal_id"] in principal.group_ids
        ):
            best = max(best, row["level"])
    return best


def permission_folder_id(conn, folder_id: int) -> Optional[int]:
    """Walk up through inheriting folders to the folder whose ACL applies."""
    seen = set()
    current = folder_id
    while current is not None and current not in seen:
        seen.add(current)
        row = conn.execute(
            "SELECT id, parent_id, inherit_permissions FROM folders WHERE id = ?", (current,)
        ).fetchone()
        if row is None:
            return None
        if not row["inherit_permissions"] or row["parent_id"] is None:
            return row["id"]
        current = row["parent_id"]
    return None


def folder_level(conn, principal: Principal, folder_id: int) -> int:
    if principal.is_admin:
        return OWNER
    acl_folder = permission_folder_id(conn, folder_id)
    if acl_folder is None:
        return 0
    return _level_from_acl(conn, "folder_permissions", "folder_id", acl_folder, principal)


def secret_acl_level(conn, principal: Principal, secret) -> int:
    """Level granted by ACLs alone (ignores the approval workflow)."""
    if principal.is_admin:
        return OWNER
    if secret["inherit_permissions"]:
        return folder_level(conn, principal, secret["folder_id"])
    return _level_from_acl(conn, "secret_permissions", "secret_id", secret["id"], principal)


def is_approver(principal: Principal, secret) -> bool:
    return secret["approver_group_id"] is not None and \
        secret["approver_group_id"] in principal.group_ids


def has_active_approval(conn, principal: Principal, secret_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM access_requests WHERE secret_id = ? AND requester_id = ? "
        "AND status = 'approved' AND expires_at > ?",
        (secret_id, principal.user_id, now_iso()),
    ).fetchone() is not None


def secret_level(conn, principal: Principal, secret) -> int:
    """Effective level, including the approval workflow."""
    level = secret_acl_level(conn, principal, secret)
    if level == VIEW and secret["require_approval"] and not is_approver(principal, secret):
        if not has_active_approval(conn, principal, secret["id"]):
            return 0
    return level


__all__ = ["VIEW", "EDIT", "OWNER", "folder_level", "secret_level", "secret_acl_level",
           "is_approver", "permission_folder_id"]
