"""
State Manager for MFT System
Provides persistent state storage for multi-instance application
"""

import json
import os
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from pathlib import Path
import threading

logger = logging.getLogger(__name__)


class StateManager:
    """Manages application state persistence for multi-instance support"""

    def __init__(self, state_dir: str = "./mft_state"):
        """
        Initialize State Manager

        Args:
            state_dir: Directory to store state files
        """
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(exist_ok=True)

        # State files
        self.ad_config_file = self.state_dir / "ad_config.json"
        self.rules_file = self.state_dir / "transfer_rules.json"
        self.compliance_file = self.state_dir / "compliance_config.json"
        self.servers_file = self.state_dir / "monitored_servers.json"
        self.app_state_file = self.state_dir / "app_state.json"

        # Thread lock for concurrent access
        self.lock = threading.Lock()

        logger.info(f"📁 State Manager initialized: {self.state_dir}")

    # ========================================================================
    # ACTIVE DIRECTORY STATE
    # ========================================================================

    def save_ad_config(self, config: Dict[str, Any]) -> bool:
        """
        Save Active Directory configuration

        Args:
            config: AD configuration dictionary

        Returns:
            True if saved successfully
        """
        try:
            with self.lock:
                # Add metadata
                state = {
                    'saved_at': datetime.now().isoformat(),
                    'config': config
                }

                with open(self.ad_config_file, 'w') as f:
                    json.dump(state, f, indent=2)

                logger.info("✅ AD configuration saved to disk")
                return True

        except Exception as e:
            logger.error(f"❌ Failed to save AD config: {e}")
            return False

    def load_ad_config(self) -> Optional[Dict[str, Any]]:
        """
        Load Active Directory configuration

        Returns:
            AD configuration dictionary or None
        """
        try:
            if not self.ad_config_file.exists():
                logger.info("ℹ️ No saved AD configuration found")
                return None

            with self.lock:
                with open(self.ad_config_file, 'r') as f:
                    state = json.load(f)

                logger.info(f"✅ AD configuration loaded (saved: {state.get('saved_at')})")
                return state.get('config')

        except Exception as e:
            logger.error(f"❌ Failed to load AD config: {e}")
            return None

    # ========================================================================
    # TRANSFER RULES STATE
    # ========================================================================

    def save_rules(self, rules: Dict[str, Any]) -> bool:
        """
        Save transfer rules

        Args:
            rules: Dictionary of transfer rules (rule_id -> rule dict)

        Returns:
            True if saved successfully
        """
        try:
            with self.lock:
                # Convert rule objects to dicts if needed
                rules_dict = {}
                for rule_id, rule in rules.items():
                    if hasattr(rule, 'to_dict'):
                        rules_dict[rule_id] = rule.to_dict()
                    elif hasattr(rule, '__dict__'):
                        rules_dict[rule_id] = {
                            k: v.value if hasattr(v, 'value') else
                               v.isoformat() if isinstance(v, datetime) else v
                            for k, v in rule.__dict__.items()
                        }
                    else:
                        rules_dict[rule_id] = rule

                state = {
                    'saved_at': datetime.now().isoformat(),
                    'rule_count': len(rules_dict),
                    'rules': rules_dict
                }

                with open(self.rules_file, 'w') as f:
                    json.dump(state, f, indent=2)

                logger.info(f"✅ {len(rules_dict)} transfer rules saved to disk")
                return True

        except Exception as e:
            logger.error(f"❌ Failed to save rules: {e}")
            import traceback
            traceback.print_exc()
            return False

    def load_rules(self) -> Optional[Dict[str, Dict[str, Any]]]:
        """
        Load transfer rules

        Returns:
            Dictionary of rules or None
        """
        try:
            if not self.rules_file.exists():
                logger.info("ℹ️ No saved transfer rules found")
                return None

            with self.lock:
                with open(self.rules_file, 'r') as f:
                    state = json.load(f)

                rules = state.get('rules', {})
                logger.info(f"✅ {len(rules)} transfer rules loaded (saved: {state.get('saved_at')})")
                return rules

        except Exception as e:
            logger.error(f"❌ Failed to load rules: {e}")
            return None

    # ========================================================================
    # COMPLIANCE STATE
    # ========================================================================

    def save_compliance_config(self, config: Dict[str, Any]) -> bool:
        """
        Save compliance configuration

        Args:
            config: Compliance configuration dictionary

        Returns:
            True if saved successfully
        """
        try:
            with self.lock:
                state = {
                    'saved_at': datetime.now().isoformat(),
                    'config': config
                }

                with open(self.compliance_file, 'w') as f:
                    json.dump(state, f, indent=2)

                logger.info("✅ Compliance configuration saved to disk")
                return True

        except Exception as e:
            logger.error(f"❌ Failed to save compliance config: {e}")
            return False

    def load_compliance_config(self) -> Optional[Dict[str, Any]]:
        """
        Load compliance configuration

        Returns:
            Compliance configuration dictionary or None
        """
        try:
            if not self.compliance_file.exists():
                logger.info("ℹ️ No saved compliance configuration found")
                return None

            with self.lock:
                with open(self.compliance_file, 'r') as f:
                    state = json.load(f)

                logger.info(f"✅ Compliance configuration loaded (saved: {state.get('saved_at')})")
                return state.get('config')

        except Exception as e:
            logger.error(f"❌ Failed to load compliance config: {e}")
            return None

    # ========================================================================
    # APPLICATION STATE
    # ========================================================================

    def save_app_state(self, state: Dict[str, Any]) -> bool:
        """
        Save general application state

        Args:
            state: Application state dictionary

        Returns:
            True if saved successfully
        """
        try:
            with self.lock:
                full_state = {
                    'saved_at': datetime.now().isoformat(),
                    'state': state
                }

                with open(self.app_state_file, 'w') as f:
                    json.dump(full_state, f, indent=2)

                logger.info("✅ Application state saved to disk")
                return True

        except Exception as e:
            logger.error(f"❌ Failed to save app state: {e}")
            return False

    def load_app_state(self) -> Optional[Dict[str, Any]]:
        """
        Load general application state

        Returns:
            Application state dictionary or None
        """
        try:
            if not self.app_state_file.exists():
                logger.info("ℹ️ No saved application state found")
                return None

            with self.lock:
                with open(self.app_state_file, 'r') as f:
                    full_state = json.load(f)

                logger.info(f"✅ Application state loaded (saved: {full_state.get('saved_at')})")
                return full_state.get('state')

        except Exception as e:
            logger.error(f"❌ Failed to load app state: {e}")
            return None

    # ========================================================================
    # UTILITY METHODS
    # ========================================================================

    def clear_all_state(self) -> bool:
        """
        Clear all saved state (reset to factory defaults)

        Returns:
            True if cleared successfully
        """
        try:
            with self.lock:
                for file in [self.ad_config_file, self.rules_file,
                           self.compliance_file, self.servers_file,
                           self.app_state_file]:
                    if file.exists():
                        file.unlink()
                        logger.info(f"🗑️ Deleted: {file.name}")

                logger.info("✅ All state cleared")
                return True

        except Exception as e:
            logger.error(f"❌ Failed to clear state: {e}")
            return False

    def get_state_summary(self) -> Dict[str, Any]:
        """
        Get summary of current saved state

        Returns:
            Dictionary with state file information
        """
        summary = {}

        files = {
            'ad_config': self.ad_config_file,
            'rules': self.rules_file,
            'compliance': self.compliance_file,
            'servers': self.servers_file,
            'app_state': self.app_state_file
        }

        for name, file_path in files.items():
            if file_path.exists():
                stat = file_path.stat()
                summary[name] = {
                    'exists': True,
                    'size': stat.st_size,
                    'modified': datetime.fromtimestamp(stat.st_mtime).isoformat()
                }
            else:
                summary[name] = {'exists': False}

        return summary
