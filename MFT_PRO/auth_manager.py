"""
Authentication Manager for MFT System
Handles local and domain user authentication
"""

import json
import os
import logging
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from dataclasses import dataclass, asdict
from pathlib import Path
import threading

logger = logging.getLogger(__name__)


@dataclass
class LocalUser:
    """Local user account"""
    user_id: str
    username: str
    password_hash: str
    full_name: str
    email: Optional[str] = None
    is_admin: bool = False
    is_system_admin: bool = False  # System admin (cannot be disabled)
    is_active: bool = True
    created_at: Optional[str] = None
    last_login: Optional[str] = None

    # MFT Permissions (same as AD users)
    can_upload: bool = False
    can_download: bool = False
    can_delete: bool = False
    can_create_rules: bool = False
    can_edit_rules: bool = False
    can_manage_users: bool = False
    can_edit_permissions: bool = False
    can_export_users: bool = False
    can_view_audit_logs: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary (excluding password hash)"""
        data = asdict(self)
        data.pop('password_hash', None)  # Never expose password hash
        return data


@dataclass
class UserSession:
    """Active user session"""
    session_id: str
    user_id: str
    username: str
    is_domain_user: bool
    is_admin: bool
    login_time: str
    last_activity: str
    ip_address: Optional[str] = None


class AuthenticationManager:
    """Manages user authentication and sessions"""

    def __init__(self, data_dir: str = "./mft_state"):
        """Initialize Authentication Manager"""
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)

        self.users_file = self.data_dir / "local_users.json"
        self.sessions_file = self.data_dir / "active_sessions.json"

        self.local_users: Dict[str, LocalUser] = {}
        self.active_sessions: Dict[str, UserSession] = {}

        self.lock = threading.Lock()

        # Load existing users
        self._load_users()

        # Create system admin if doesn't exist
        self._ensure_system_admin()

        logger.info("🔐 Authentication Manager initialized")

    # ========================================================================
    # PASSWORD HASHING
    # ========================================================================

    @staticmethod
    def hash_password(password: str, salt: Optional[str] = None) -> tuple:
        """
        Hash password with salt

        Returns:
            (password_hash, salt)
        """
        if salt is None:
            salt = secrets.token_hex(32)

        # Use PBKDF2 with SHA-256
        pwd_hash = hashlib.pbkdf2_hmac(
            'sha256',
            password.encode('utf-8'),
            salt.encode('utf-8'),
            100000  # iterations
        )

        return f"{salt}${pwd_hash.hex()}", salt

    @staticmethod
    def verify_password(password: str, stored_hash: str) -> bool:
        """Verify password against stored hash"""
        try:
            salt, pwd_hash = stored_hash.split('$')
            test_hash, _ = AuthenticationManager.hash_password(password, salt)
            return test_hash == stored_hash
        except Exception as e:
            logger.error(f"Password verification error: {e}")
            return False

    # ========================================================================
    # USER MANAGEMENT
    # ========================================================================

    def _ensure_system_admin(self):
        """Ensure system admin account exists"""
        # Check if system admin exists
        system_admin = None
        for user in self.local_users.values():
            if user.is_system_admin:
                system_admin = user
                break

        if not system_admin:
            # Create default system admin
            logger.info("Creating default system admin account")

            user_id = "sysadmin-001"
            default_password = "Admin@123"  # User should change this

            pwd_hash, _ = self.hash_password(default_password)

            admin = LocalUser(
                user_id=user_id,
                username="sysadmin",
                password_hash=pwd_hash,
                full_name="System Administrator",
                email="admin@mft.local",
                is_admin=True,
                is_system_admin=True,
                is_active=True,
                created_at=datetime.now().isoformat()
            )

            self.local_users[user_id] = admin
            self._save_users()

            logger.warning("⚠️ DEFAULT SYSTEM ADMIN CREATED")
            logger.warning(f"   Username: sysadmin")
            logger.warning(f"   Password: {default_password}")
            logger.warning("   ⚠️ CHANGE THIS PASSWORD IMMEDIATELY!")

    def create_local_user(
        self,
        username: str,
        password: str,
        full_name: str,
        email: Optional[str] = None,
        is_admin: bool = False
    ) -> Dict:
        """Create a new local user account"""
        try:
            with self.lock:
                # Check if username exists
                for user in self.local_users.values():
                    if user.username.lower() == username.lower():
                        return {
                            'success': False,
                            'error': 'Username already exists'
                        }

                # Generate user ID
                user_id = f"local-{secrets.token_hex(8)}"

                # Hash password
                pwd_hash, _ = self.hash_password(password)

                # Create user
                user = LocalUser(
                    user_id=user_id,
                    username=username,
                    password_hash=pwd_hash,
                    full_name=full_name,
                    email=email,
                    is_admin=is_admin,
                    is_system_admin=False,
                    is_active=True,
                    created_at=datetime.now().isoformat()
                )

                self.local_users[user_id] = user
                self._save_users()

                logger.info(f"✅ Local user created: {username}")

                return {
                    'success': True,
                    'user_id': user_id,
                    'user': user.to_dict()
                }

        except Exception as e:
            logger.error(f"Failed to create user: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def update_local_user_permissions(
        self,
        user_id: str,
        can_upload: Optional[bool] = None,
        can_download: Optional[bool] = None,
        can_delete: Optional[bool] = None,
        can_create_rules: Optional[bool] = None,
        can_edit_rules: Optional[bool] = None,
        can_manage_users: Optional[bool] = None,
        can_edit_permissions: Optional[bool] = None,
        can_export_users: Optional[bool] = None,
        can_view_audit_logs: Optional[bool] = None,
        is_admin: Optional[bool] = None
    ) -> Dict:
        """Update local user permissions"""
        try:
            with self.lock:
                if user_id not in self.local_users:
                    return {
                        'success': False,
                        'error': 'User not found'
                    }

                user = self.local_users[user_id]

                # Update permissions
                if can_upload is not None:
                    user.can_upload = can_upload
                if can_download is not None:
                    user.can_download = can_download
                if can_delete is not None:
                    user.can_delete = can_delete
                if can_create_rules is not None:
                    user.can_create_rules = can_create_rules
                if can_edit_rules is not None:
                    user.can_edit_rules = can_edit_rules
                if can_manage_users is not None:
                    user.can_manage_users = can_manage_users
                if can_edit_permissions is not None:
                    user.can_edit_permissions = can_edit_permissions
                if can_export_users is not None:
                    user.can_export_users = can_export_users
                if can_view_audit_logs is not None:
                    user.can_view_audit_logs = can_view_audit_logs
                if is_admin is not None:
                    user.is_admin = is_admin

                self._save_users()

                logger.info(f"✅ Updated permissions for local user: {user.username}")

                return {
                    'success': True,
                    'user': user.to_dict()
                }

        except Exception as e:
            logger.error(f"Failed to update permissions: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def get_local_user(self, user_id: str) -> Optional[LocalUser]:
        """Get a specific local user by ID"""
        return self.local_users.get(user_id)

    def disable_local_accounts(self, exclude_system_admin: bool = True):
        """
        Disable all local accounts (except system admin)
        Called when AD sync is successful
        """
        try:
            with self.lock:
                count = 0
                for user in self.local_users.values():
                    if exclude_system_admin and user.is_system_admin:
                        continue

                    if user.is_active:
                        user.is_active = False
                        count += 1

                self._save_users()
                logger.info(f"🔒 Disabled {count} local accounts (AD sync active)")

                return {
                    'success': True,
                    'disabled_count': count
                }

        except Exception as e:
            logger.error(f"Failed to disable accounts: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    def enable_local_accounts(self):
        """Enable all local accounts (when AD is disconnected)"""
        try:
            with self.lock:
                count = 0
                for user in self.local_users.values():
                    if not user.is_active:
                        user.is_active = True
                        count += 1

                self._save_users()
                logger.info(f"🔓 Enabled {count} local accounts (AD disconnected)")

                return {
                    'success': True,
                    'enabled_count': count
                }

        except Exception as e:
            logger.error(f"Failed to enable accounts: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    # ========================================================================
    # AUTHENTICATION
    # ========================================================================

    def authenticate_local(self, username: str, password: str, ip_address: Optional[str] = None) -> Dict:
        """Authenticate local user"""
        try:
            # Find user
            user = None
            for u in self.local_users.values():
                if u.username.lower() == username.lower():
                    user = u
                    break

            if not user:
                logger.warning(f"❌ Login failed: User '{username}' not found")
                return {
                    'success': False,
                    'error': 'Invalid username or password'
                }

            # Check if account is active
            if not user.is_active:
                logger.warning(f"❌ Login failed: Account '{username}' is disabled")
                return {
                    'success': False,
                    'error': 'Account is disabled. Please use domain credentials.'
                }

            # Verify password
            if not self.verify_password(password, user.password_hash):
                logger.warning(f"❌ Login failed: Invalid password for '{username}'")
                return {
                    'success': False,
                    'error': 'Invalid username or password'
                }

            # Create session
            session_id = secrets.token_urlsafe(32)

            session = UserSession(
                session_id=session_id,
                user_id=user.user_id,
                username=user.username,
                is_domain_user=False,
                is_admin=user.is_admin,
                login_time=datetime.now().isoformat(),
                last_activity=datetime.now().isoformat(),
                ip_address=ip_address
            )

            with self.lock:
                self.active_sessions[session_id] = session
                user.last_login = datetime.now().isoformat()
                self._save_users()
                self._save_sessions()

            logger.info(f"✅ Local user logged in: {username}")

            return {
                'success': True,
                'session_id': session_id,
                'user': user.to_dict(),
                'is_domain_user': False
            }

        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return {
                'success': False,
                'error': 'Authentication failed'
            }

    def authenticate_domain(self, username: str, password: str, ad_manager, ip_address: Optional[str] = None) -> Dict:
        """Authenticate domain user"""
        try:
            # Verify domain user exists in AD
            domain_user = None
            for user in ad_manager.users.values():
                if user.username.lower() == username.lower():
                    domain_user = user
                    break

            if not domain_user:
                logger.warning(f"❌ Domain login failed: User '{username}' not found in AD")
                return {
                    'success': False,
                    'error': 'Invalid domain credentials'
                }

            # In production, you would verify against actual AD
            # For now, we'll accept any password for domain users
            # TODO: Implement actual AD authentication

            # Create session
            session_id = secrets.token_urlsafe(32)

            session = UserSession(
                session_id=session_id,
                user_id=domain_user.user_id,
                username=domain_user.username,
                is_domain_user=True,
                is_admin=domain_user.is_admin,
                login_time=datetime.now().isoformat(),
                last_activity=datetime.now().isoformat(),
                ip_address=ip_address
            )

            with self.lock:
                self.active_sessions[session_id] = session
                self._save_sessions()

            logger.info(f"✅ Domain user logged in: {username}")

            return {
                'success': True,
                'session_id': session_id,
                'user': domain_user.to_dict(),
                'is_domain_user': True
            }

        except Exception as e:
            logger.error(f"Domain authentication error: {e}")
            return {
                'success': False,
                'error': 'Domain authentication failed'
            }

    def logout(self, session_id: str) -> Dict:
        """Logout user session"""
        try:
            with self.lock:
                if session_id in self.active_sessions:
                    session = self.active_sessions[session_id]
                    logger.info(f"🚪 User logged out: {session.username}")
                    del self.active_sessions[session_id]
                    self._save_sessions()

                    return {'success': True}
                else:
                    return {'success': False, 'error': 'Session not found'}

        except Exception as e:
            logger.error(f"Logout error: {e}")
            return {'success': False, 'error': str(e)}

    def validate_session(self, session_id: str) -> Optional[UserSession]:
        """Validate and return active session"""
        if session_id in self.active_sessions:
            session = self.active_sessions[session_id]

            # Update last activity
            session.last_activity = datetime.now().isoformat()
            self._save_sessions()

            return session

        return None

    def get_active_sessions(self) -> List[UserSession]:
        """Get all active sessions"""
        return list(self.active_sessions.values())

    def get_local_users(self) -> List[Dict]:
        """Get all local users (excluding password hashes)"""
        return [user.to_dict() for user in self.local_users.values()]

    # ========================================================================
    # PERSISTENCE
    # ========================================================================

    def _load_users(self):
        """Load local users from disk"""
        try:
            if self.users_file.exists():
                with open(self.users_file, 'r') as f:
                    data = json.load(f)

                    for user_id, user_data in data.items():
                        self.local_users[user_id] = LocalUser(**user_data)

                    logger.info(f"📋 Loaded {len(self.local_users)} local users")

        except Exception as e:
            logger.error(f"Failed to load users: {e}")

    def _save_users(self):
        """Save local users to disk"""
        try:
            data = {}
            for user_id, user in self.local_users.items():
                data[user_id] = asdict(user)

            with open(self.users_file, 'w') as f:
                json.dump(data, f, indent=2)

        except Exception as e:
            logger.error(f"Failed to save users: {e}")

    def _save_sessions(self):
        """Save active sessions to disk"""
        try:
            data = {}
            for session_id, session in self.active_sessions.items():
                data[session_id] = asdict(session)

            with open(self.sessions_file, 'w') as f:
                json.dump(data, f, indent=2)

        except Exception as e:
            logger.error(f"Failed to save sessions: {e}")
