# Compliance Encryption Guide

## Overview

The MFT system provides two types of encryption to meet compliance requirements:

1. **Encryption in Transit** - Data encrypted while being transferred over the network
2. **Encryption at Rest** - Data encrypted while stored on disk

## Current Implementation Status

### ✅ Encryption in Transit (Partial)

| Protocol | Encryption Status | Algorithm | Notes |
|----------|------------------|-----------|-------|
| **SFTP** | ✅ **Encrypted** | SSH/TLS | Fully encrypted, recommended |
| **FTPS** | ✅ **Encrypted** | TLS/SSL | Fully encrypted |
| **HTTPS** | ✅ **Encrypted** | TLS/SSL | Fully encrypted |
| **WebDAV HTTPS** | ✅ **Encrypted** | TLS/SSL | Fully encrypted |
| **SMB** | ⚠️ **Conditional** | SMB3 encryption | Only if SMB3 with encryption enabled |
| **UNC** | ❌ **Not Encrypted** | None | Plain SMB, not encrypted |
| **FTP** | ❌ **Not Encrypted** | None | Plain FTP, use FTPS instead |
| **HTTP** | ❌ **Not Encrypted** | None | Plain HTTP, use HTTPS instead |
| **Local** | ❌ **Not Encrypted** | None | Local filesystem |

### 🚧 Encryption at Rest (Available)

- **Status**: Implemented in `encryption_manager.py`
- **Algorithm**: AES-256 via Fernet (symmetric encryption)
- **Key Management**: Encryption key stored in `mft_encryption.key`
- **Usage**: Can be enabled per compliance framework

## Compliance Framework Settings

Each compliance framework has specific encryption requirements:

```python
@dataclass
class ComplianceConfig:
    encryption_required: bool = False          # Overall encryption requirement
    encryption_algorithm: Optional[str] = None # Preferred algorithm
    data_encryption_at_rest: bool = False      # Encrypt files on disk
    data_encryption_in_transit: bool = True    # Encrypt during transfer
```

### Framework-Specific Requirements

| Framework | In-Transit | At-Rest | Notes |
|-----------|-----------|---------|-------|
| **HIPAA** | ✅ Required | ✅ Recommended | PHI must be encrypted |
| **PCI DSS** | ✅ Required | ✅ Required | Cardholder data must be encrypted |
| **GDPR** | ✅ Required | ⚠️ Recommended | Personal data protection |
| **SOC II** | ✅ Required | ⚠️ Recommended | Trust Service Criteria |
| **ISO 27001** | ✅ Required | ✅ Required | Information security |
| **GLBA** | ✅ Required | ⚠️ Recommended | Financial data protection |
| **CFR Part 11** | ✅ Required | ✅ Required | Electronic records/signatures |

## How to Verify Encryption

### Method 1: Using the Encryption Manager

```python
from encryption_manager import EncryptionManager
from compliance_system import ComplianceManager

# Initialize
compliance_mgr = ComplianceManager()
enc_manager = EncryptionManager(compliance_mgr)

# Verify a transfer protocol
status = enc_manager.verify_transfer_encryption('sftp', config)

print(f"Protocol: {status.protocol}")
print(f"Encrypted in transit: {status.encryption_in_transit}")
print(f"Encryption algorithm: {status.encryption_algorithm}")
print(f"Compliant: {status.is_compliant}")

if status.warnings:
    for warning in status.warnings:
        print(f"⚠️  {warning}")
```

### Method 2: Network Traffic Analysis

**For SFTP (Encrypted):**
```bash
# Capture traffic on port 22
tcpdump -i any port 22 -w sftp_capture.pcap

# Analysis:
# - Traffic should be encrypted (SSH protocol)
# - Cannot read file contents in packet capture
# - Shows SSH handshake and encrypted data
```

**For UNC/SMB (Not Encrypted):**
```bash
# Capture traffic on port 445
tcpdump -i any port 445 -w smb_capture.pcap

# Analysis:
# - Traffic is plain SMB
# - File contents may be visible in packet capture
# - NOT RECOMMENDED for sensitive data
```

### Method 3: Check Compliance Logs

```python
# Check audit log for encryption events
GET /api/v1/audit/events?event_type=encryption_enabled

# Response shows when encryption was enabled
{
  "events": [
    {
      "timestamp": "2025-11-29T12:00:00",
      "event_type": "encryption_enabled",
      "action": "Encryption enabled for HIPAA compliance",
      "result": "success"
    }
  ]
}
```

### Method 4: Test File Encryption

```python
from encryption_manager import EncryptionManager

enc_manager = EncryptionManager()

# Encrypt a file
encrypted_file = enc_manager.encrypt_file_at_rest("sensitive.txt")
# Creates: sensitive.txt.enc

# Verify the encrypted file is not readable
with open(encrypted_file, 'r') as f:
    content = f.read()
    # Should see gibberish/encrypted bytes

# Decrypt to verify
decrypted_file = enc_manager.decrypt_file_at_rest(encrypted_file)
# Creates: sensitive.txt (original content restored)
```

## How Compliance Encryption Should Work

### Recommended Setup for Each Compliance Framework

#### HIPAA (Health Insurance Portability and Accountability Act)

```python
# Enable HIPAA compliance
compliance_mgr.enable_framework(
    ComplianceFramework.HIPAA,
    encryption_required=True,
    encryption_algorithm=EncryptionAlgorithm.AES_256_GCM,
    data_encryption_in_transit=True,
    data_encryption_at_rest=True,  # Recommended for PHI
    audit_retention_days=2555  # 7 years
)

# Use SFTP for transfers (encrypted in transit)
# Enable at-rest encryption for stored files
```

**Verification:**
```bash
# All PHI transfers must use encrypted protocols
✅ SFTP ✅ FTPS ❌ UNC ❌ FTP
```

#### PCI DSS (Payment Card Industry Data Security Standard)

```python
# Enable PCI DSS compliance
compliance_mgr.enable_framework(
    ComplianceFramework.PCI_DSS,
    encryption_required=True,
    encryption_algorithm=EncryptionAlgorithm.AES_256_GCM,
    data_encryption_in_transit=True,  # Required
    data_encryption_at_rest=True,     # Required
    pci_dss_level=1  # Merchant level
)

# MUST use encrypted protocols
# MUST encrypt cardholder data at rest
```

**Verification:**
```bash
# Cardholder data must NEVER be transmitted unencrypted
✅ SFTP ✅ FTPS ❌ ALL UNENCRYPTED PROTOCOLS
```

#### GDPR (General Data Protection Regulation)

```python
# Enable GDPR compliance
compliance_mgr.enable_framework(
    ComplianceFramework.GDPR,
    encryption_required=True,
    encryption_algorithm=EncryptionAlgorithm.AES_256_GCM,
    data_encryption_in_transit=True,
    data_encryption_at_rest=False,  # Recommended but not strictly required
    gdpr_lawful_basis="consent"
)
```

**Verification:**
```bash
# Personal data should be encrypted in transit
✅ Encrypted protocols preferred
⚠️  Document why unencrypted protocols are used (if any)
```

## Encryption Enforcement Flow

```
┌─────────────────────────────────────────────────────────────┐
│  1. User creates transfer rule                              │
└─────────────┬───────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Check compliance requirements                           │
│     - Is encryption required?                               │
│     - Which framework(s) are enabled?                       │
└─────────────┬───────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────┐
│  3. Verify protocol encryption                              │
│     - SFTP = ✅ Encrypted in transit                       │
│     - UNC = ❌ NOT encrypted                                │
└─────────────┬───────────────────────────────────────────────┘
              │
              ▼
         ┌────┴────┐
         │         │
    Compliant  Non-Compliant
         │         │
         │         ▼
         │    ┌─────────────────────────────────────────┐
         │    │  4a. BLOCK transfer OR WARN user       │
         │    │      - Log compliance violation         │
         │    │      - Suggest encrypted alternative    │
         │    └─────────────────────────────────────────┘
         │
         ▼
    ┌─────────────────────────────────────────────────────────┐
    │  4b. Proceed with transfer                              │
    │      - Log encryption status                            │
    │      - Apply at-rest encryption if required             │
    └─────────────────────────────────────────────────────────┘
```

## Testing Encryption

### Test Script

```python
#!/usr/bin/env python3
"""Test encryption compliance"""

from encryption_manager import EncryptionManager
from compliance_system import ComplianceManager, ComplianceFramework

def test_encryption():
    print("=" * 60)
    print("MFT ENCRYPTION COMPLIANCE TEST")
    print("=" * 60)

    # Initialize managers
    compliance_mgr = ComplianceManager()
    enc_manager = EncryptionManager(compliance_mgr)

    # Enable HIPAA (requires encryption)
    print("\n1. Enabling HIPAA compliance (requires encryption)...")
    compliance_mgr.enable_framework(
        ComplianceFramework.HIPAA,
        encryption_required=True,
        data_encryption_in_transit=True
    )

    # Test protocols
    protocols = [
        ('sftp', True),   # Should pass
        ('unc', False),   # Should fail
        ('ftps', True),   # Should pass
        ('ftp', False)    # Should fail
    ]

    print("\n2. Testing protocol compliance...")
    for protocol, should_pass in protocols:
        status = enc_manager.verify_transfer_encryption(protocol, None)

        result = "✅ PASS" if status.is_compliant == should_pass else "❌ FAIL"
        print(f"   {protocol.upper():10} - Encrypted: {status.encryption_in_transit:5} - {result}")

        if status.warnings:
            for warning in status.warnings:
                print(f"      ⚠️  {warning}")

    # Test file encryption
    print("\n3. Testing file encryption at rest...")
    test_file = "test_sensitive_data.txt"

    # Create test file
    with open(test_file, 'w') as f:
        f.write("CONFIDENTIAL: Patient PHI Data")

    # Encrypt
    encrypted = enc_manager.encrypt_file_at_rest(test_file)
    print(f"   ✅ File encrypted: {encrypted}")

    # Decrypt
    decrypted = enc_manager.decrypt_file_at_rest(encrypted)
    print(f"   ✅ File decrypted: {decrypted}")

    # Verify
    with open(test_file, 'r') as f:
        original = f.read()
    with open(decrypted, 'r') as f:
        restored = f.read()

    if original == restored:
        print("   ✅ Encryption/decryption successful!")
    else:
        print("   ❌ Encryption/decryption FAILED!")

    # Cleanup
    import os
    os.remove(test_file)
    os.remove(encrypted)
    os.remove(decrypted)

    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    test_encryption()
```

## Recommendations

### For Maximum Security:

1. **Always use SFTP for sensitive data** - It's encrypted by default
2. **Enable encryption at rest** for PCI/HIPAA/CFR Part 11 data
3. **Avoid UNC/SMB** for sensitive data unless SMB3 encryption is enabled
4. **Never use FTP** - Always use FTPS or SFTP instead
5. **Monitor compliance violations** via audit logs
6. **Backup encryption keys** stored in `mft_encryption.key`

### Protocol Selection Guide:

```
Sensitive Data (HIPAA, PCI, PHI):
  ✅ SFTP (best choice)
  ✅ FTPS (good choice)
  ❌ UNC/SMB (avoid)
  ❌ FTP (never use)

Internal/Non-Sensitive:
  ✅ UNC/SMB (acceptable)
  ✅ Local (acceptable)
  ⚠️  Still consider SFTP for consistency

Public Internet:
  ✅ SFTP (required)
  ✅ HTTPS (required)
  ❌ NEVER use unencrypted protocols
```

## Troubleshooting

### Issue: "Protocol does not support encryption"

**Solution**: Switch to an encrypted protocol

```python
# Instead of UNC:
protocol='unc'  # ❌ Not encrypted

# Use SFTP:
protocol='sftp'  # ✅ Encrypted
host='10.10.100.4'
port=22
```

### Issue: "Compliance violation - encryption required"

**Solution**: Either:
1. Use encrypted protocol (SFTP, FTPS)
2. Disable compliance framework (if appropriate)
3. Document exception in audit log

### Issue: "Cannot read encrypted file"

**Solution**: Files encrypted at rest must be decrypted before use

```python
# Decrypt file
enc_manager.decrypt_file_at_rest("file.txt.enc", "file.txt")
```

## API Endpoints for Verification

```bash
# Check encryption status of a protocol
POST /api/v1/compliance/verify-encryption
{
  "protocol": "sftp",
  "host": "10.10.100.4",
  "port": 22
}

Response:
{
  "encryption_in_transit": true,
  "encryption_algorithm": "SSH/TLS",
  "is_compliant": true,
  "warnings": []
}

# Get encryption recommendations
GET /api/v1/compliance/encryption-recommendations?protocol=unc

Response:
{
  "current_protocol": "unc",
  "encryption_status": "unencrypted",
  "recommendations": [
    {
      "priority": "HIGH",
      "issue": "UNC does not encrypt data in transit",
      "recommendation": "Switch to encrypted protocol",
      "alternatives": ["SFTP", "SMB3 with encryption"]
    }
  ]
}
```

## Conclusion

**The MFT system provides:**
- ✅ **Encryption in transit** via encrypted protocols (SFTP, FTPS, HTTPS)
- ✅ **Encryption at rest** via AES-256 (Fernet)
- ✅ **Compliance verification** for enabled frameworks
- ✅ **Audit logging** of all encryption events

**To verify encryption is working:**
1. Use the `EncryptionManager` to verify protocols
2. Check audit logs for encryption events
3. Capture network traffic and verify it's encrypted
4. Test file encryption/decryption

**Remember:**
- SFTP = Always encrypted ✅
- UNC/SMB = Not encrypted ❌ (unless SMB3)
- Use encrypted protocols for all sensitive data
- Enable at-rest encryption for PCI/HIPAA compliance
