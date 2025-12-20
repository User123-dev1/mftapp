"""
Encryption Manager for MFT System
Handles encryption verification and enforcement for compliance
"""

import os
import hashlib
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from dataclasses import dataclass
from typing import Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class EncryptionStatus:
    """Status of encryption for a transfer"""
    protocol: str
    encryption_in_transit: bool
    encryption_at_rest: bool
    encryption_algorithm: Optional[str]
    is_compliant: bool
    compliance_frameworks: list
    warnings: list


class EncryptionManager:
    """Manages encryption verification and enforcement"""

    # Protocols that provide inherent encryption in transit
    ENCRYPTED_PROTOCOLS = {
        'sftp': 'SSH/TLS',
        'ftps': 'TLS/SSL',
        'https': 'TLS/SSL',
        'webdav_https': 'TLS/SSL'
    }

    # Protocols that do NOT provide encryption
    UNENCRYPTED_PROTOCOLS = {
        'ftp': 'No encryption',
        'http': 'No encryption',
        'unc': 'No encryption (plain SMB)',
        'smb': 'No encryption unless SMB3 with encryption',
        'local': 'No encryption (local filesystem)'
    }

    def __init__(self, compliance_manager=None):
        """
        Initialize encryption manager

        Args:
            compliance_manager: ComplianceManager instance for policy enforcement
        """
        self.compliance_manager = compliance_manager
        self.encryption_key = None
        self._load_or_generate_key()

    def _load_or_generate_key(self):
        """Load or generate encryption key for at-rest encryption"""
        key_file = "mft_encryption.key"

        if os.path.exists(key_file):
            with open(key_file, 'rb') as f:
                self.encryption_key = f.read()
            logger.info("🔑 Loaded encryption key from file")
        else:
            # Generate new key
            self.encryption_key = Fernet.generate_key()
            with open(key_file, 'wb') as f:
                f.write(self.encryption_key)
            logger.warning("⚠️  Generated new encryption key - BACKUP THIS FILE!")
            logger.info(f"🔑 Encryption key saved to: {key_file}")

    def verify_transfer_encryption(self, protocol: str, config) -> EncryptionStatus:
        """
        Verify if a transfer will use encryption

        Args:
            protocol: Transfer protocol (sftp, unc, smb, etc.)
            config: TransferConfig object

        Returns:
            EncryptionStatus with verification details
        """
        protocol_lower = protocol.lower()
        warnings = []

        # Check encryption in transit
        encryption_in_transit = False
        encryption_algorithm = None

        if protocol_lower in self.ENCRYPTED_PROTOCOLS:
            encryption_in_transit = True
            encryption_algorithm = self.ENCRYPTED_PROTOCOLS[protocol_lower]
            logger.info(f"✅ Protocol {protocol} provides encryption in transit: {encryption_algorithm}")

        elif protocol_lower in self.UNENCRYPTED_PROTOCOLS:
            encryption_in_transit = False
            reason = self.UNENCRYPTED_PROTOCOLS[protocol_lower]
            logger.warning(f"⚠️  Protocol {protocol} does NOT provide encryption: {reason}")
            warnings.append(f"Protocol {protocol} does not encrypt data in transit: {reason}")

        # Check encryption at rest (not yet implemented)
        encryption_at_rest = False

        # Check compliance requirements
        is_compliant = True
        required_frameworks = []

        if self.compliance_manager:
            # Check if any enabled framework requires encryption
            if self.compliance_manager.is_encryption_required():
                required_frameworks = [
                    f.framework.value
                    for f in self.compliance_manager.frameworks.values()
                    if f.enabled and f.encryption_required
                ]

                # Check encryption in transit requirement
                for framework_config in self.compliance_manager.frameworks.values():
                    if framework_config.enabled and framework_config.data_encryption_in_transit:
                        if not encryption_in_transit:
                            is_compliant = False
                            warnings.append(
                                f"Compliance framework {framework_config.framework.value} "
                                f"requires encryption in transit, but {protocol} does not provide it"
                            )

                    # Check encryption at rest requirement
                    if framework_config.enabled and framework_config.data_encryption_at_rest:
                        if not encryption_at_rest:
                            warnings.append(
                                f"Compliance framework {framework_config.framework.value} "
                                f"requires encryption at rest (not yet implemented)"
                            )

        return EncryptionStatus(
            protocol=protocol,
            encryption_in_transit=encryption_in_transit,
            encryption_at_rest=encryption_at_rest,
            encryption_algorithm=encryption_algorithm,
            is_compliant=is_compliant,
            compliance_frameworks=required_frameworks,
            warnings=warnings
        )

    def encrypt_file_at_rest(self, file_path: str, output_path: Optional[str] = None) -> str:
        """
        Encrypt a file at rest using AES-256

        Args:
            file_path: Path to file to encrypt
            output_path: Optional output path (defaults to file_path + .enc)

        Returns:
            Path to encrypted file
        """
        if output_path is None:
            output_path = file_path + '.enc'

        fernet = Fernet(self.encryption_key)

        # Read file
        with open(file_path, 'rb') as f:
            data = f.read()

        # Encrypt
        encrypted_data = fernet.encrypt(data)

        # Write encrypted file
        with open(output_path, 'wb') as f:
            f.write(encrypted_data)

        logger.info(f"🔒 Encrypted file: {file_path} -> {output_path}")
        return output_path

    def decrypt_file_at_rest(self, encrypted_file_path: str, output_path: Optional[str] = None) -> str:
        """
        Decrypt a file that was encrypted at rest

        Args:
            encrypted_file_path: Path to encrypted file
            output_path: Optional output path (defaults to removing .enc extension)

        Returns:
            Path to decrypted file
        """
        if output_path is None:
            if encrypted_file_path.endswith('.enc'):
                output_path = encrypted_file_path[:-4]
            else:
                output_path = encrypted_file_path + '.dec'

        fernet = Fernet(self.encryption_key)

        # Read encrypted file
        with open(encrypted_file_path, 'rb') as f:
            encrypted_data = f.read()

        # Decrypt
        decrypted_data = fernet.decrypt(encrypted_data)

        # Write decrypted file
        with open(output_path, 'wb') as f:
            f.write(decrypted_data)

        logger.info(f"🔓 Decrypted file: {encrypted_file_path} -> {output_path}")
        return output_path

    def get_encryption_recommendations(self, protocol: str) -> dict:
        """
        Get recommendations for improving encryption

        Args:
            protocol: Current protocol being used

        Returns:
            Dictionary with recommendations
        """
        protocol_lower = protocol.lower()
        recommendations = {
            'current_protocol': protocol,
            'encryption_status': 'encrypted' if protocol_lower in self.ENCRYPTED_PROTOCOLS else 'unencrypted',
            'recommendations': []
        }

        if protocol_lower in self.UNENCRYPTED_PROTOCOLS:
            recommendations['recommendations'].append({
                'priority': 'HIGH',
                'issue': f'{protocol} does not encrypt data in transit',
                'recommendation': 'Switch to encrypted protocol',
                'alternatives': []
            })

            # Suggest alternatives
            if protocol_lower == 'ftp':
                recommendations['recommendations'][-1]['alternatives'] = ['FTPS', 'SFTP']
            elif protocol_lower in ['unc', 'smb']:
                recommendations['recommendations'][-1]['alternatives'] = ['SFTP', 'SMB3 with encryption']
            elif protocol_lower == 'http':
                recommendations['recommendations'][-1]['alternatives'] = ['HTTPS']

        # Add at-rest encryption recommendation if compliance requires it
        if self.compliance_manager and self.compliance_manager.is_encryption_required():
            recommendations['recommendations'].append({
                'priority': 'MEDIUM',
                'issue': 'Encryption at rest not implemented',
                'recommendation': 'Enable file encryption before storage',
                'alternatives': ['AES-256 encryption', 'Hardware encryption']
            })

        return recommendations


# Example usage and testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Create encryption manager
    enc_manager = EncryptionManager()

    # Test protocol verification
    protocols = ['sftp', 'unc', 'smb', 'ftps', 'ftp']

    for protocol in protocols:
        print(f"\n{'='*60}")
        print(f"Testing: {protocol}")
        print(f"{'='*60}")

        status = enc_manager.verify_transfer_encryption(protocol, None)
        print(f"Encryption in transit: {status.encryption_in_transit}")
        print(f"Algorithm: {status.encryption_algorithm}")
        print(f"Compliant: {status.is_compliant}")
        if status.warnings:
            print("Warnings:")
            for warning in status.warnings:
                print(f"  - {warning}")

        # Get recommendations
        recommendations = enc_manager.get_encryption_recommendations(protocol)
        if recommendations['recommendations']:
            print("\nRecommendations:")
            for rec in recommendations['recommendations']:
                print(f"  [{rec['priority']}] {rec['issue']}")
                print(f"    → {rec['recommendation']}")
                if rec.get('alternatives'):
                    print(f"    Alternatives: {', '.join(rec['alternatives'])}")

    # Test file encryption/decryption
    print(f"\n{'='*60}")
    print("Testing File Encryption")
    print(f"{'='*60}")

    # Create test file
    test_file = "test_encryption.txt"
    with open(test_file, 'w') as f:
        f.write("This is sensitive data that should be encrypted!")

    # Encrypt
    encrypted_file = enc_manager.encrypt_file_at_rest(test_file)
    print(f"Encrypted file created: {encrypted_file}")

    # Decrypt
    decrypted_file = enc_manager.decrypt_file_at_rest(encrypted_file)
    print(f"Decrypted file created: {decrypted_file}")

    # Verify contents match
    with open(test_file, 'r') as f:
        original = f.read()
    with open(decrypted_file, 'r') as f:
        decrypted = f.read()

    if original == decrypted:
        print("✅ Encryption/Decryption test PASSED")
    else:
        print("❌ Encryption/Decryption test FAILED")

    # Cleanup
    os.remove(test_file)
    os.remove(encrypted_file)
    os.remove(decrypted_file)
