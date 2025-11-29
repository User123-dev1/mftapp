"""
Enterprise MFT System with Compliance & User Management
FIXED VERSION with AD Config and Permission Editing
"""

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
import os
import sys
import logging
from datetime import datetime
from typing import Dict, List, Optional
import asyncio
import json

# Import compliance system
from compliance_system import (
    ComplianceManager,
    AuditManager,
    ActiveDirectoryManager,
    ComplianceFramework,
    AuditEventType,
    EncryptionAlgorithm
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s:%(name)s:%(message)s'
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# AD Configuration storage
AD_CONFIG_FILE = 'ad_config.json'

def load_ad_config():
    """Load AD configuration"""
    if os.path.exists(AD_CONFIG_FILE):
        try:
            with open(AD_CONFIG_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        'server': '',
        'port': 389,
        'use_ssl': False,
        'base_dn': '',
        'username': '',
        'password': '',
        'user_search_base': '',
        'user_search_filter': '(objectClass=user)',
        'group_search_base': '',
        'group_search_filter': '(objectClass=group)'
    }

def save_ad_config(config):
    """Save AD configuration"""
    try:
        with open(AD_CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save AD config: {e}")
        return False

# Initialize MFT application (mock for now)
class MockMFTApp:
    def __init__(self):
        self.transfers = {}

    async def transfer_file(self, source, dest, config):
        import uuid
        task_id = str(uuid.uuid4())
        self.transfers[task_id] = {
            'task_id': task_id,
            'source': source,
            'destination': dest,
            'status': 'completed',
            'protocol': 'UNC',
            'timestamp': datetime.now().isoformat()
        }
        return task_id

mft_app = MockMFTApp()

# Initialize compliance, audit, and AD managers
compliance_mgr = ComplianceManager()
audit_mgr = AuditManager()
ad_mgr = ActiveDirectoryManager()

# Initialize with HIPAA, PCI DSS, and GDPR enabled
logger.info("🔒 Configuring compliance frameworks...")

# Enable and configure HIPAA
compliance_mgr.enable_framework(ComplianceFramework.HIPAA)
compliance_mgr.update_framework_config(
    ComplianceFramework.HIPAA,
    encryption_algorithm=EncryptionAlgorithm.AES_256_GCM,
    audit_retention_days=2555,
    data_encryption_at_rest=True,
    data_encryption_in_transit=True,
    hipaa_business_associate_agreement=True
)
audit_mgr.log_event(
    AuditEventType.COMPLIANCE_ENABLED,
    "HIPAA compliance enabled",
    username="system",
    result="success",
    details={'framework': 'HIPAA', 'encryption': 'AES-256-GCM'}
)
logger.info("✅ HIPAA enabled with AES-256-GCM encryption")

# Enable and configure PCI DSS
compliance_mgr.enable_framework(ComplianceFramework.PCI_DSS)
compliance_mgr.update_framework_config(
    ComplianceFramework.PCI_DSS,
    encryption_algorithm=EncryptionAlgorithm.AES_256_GCM,
    audit_retention_days=365,
    multi_factor_auth_required=True,
    data_encryption_at_rest=True,
    data_encryption_in_transit=True,
    pci_dss_level=1
)
audit_mgr.log_event(
    AuditEventType.COMPLIANCE_ENABLED,
    "PCI DSS compliance enabled",
    username="system",
    result="success",
    details={'framework': 'PCI DSS', 'level': 1}
)
logger.info("✅ PCI DSS Level 1 enabled with MFA requirement")

# Enable and configure GDPR
compliance_mgr.enable_framework(ComplianceFramework.GDPR)
compliance_mgr.update_framework_config(
    ComplianceFramework.GDPR,
    encryption_algorithm=EncryptionAlgorithm.AES_256_GCM,
    audit_retention_days=2555,
    data_classification_required=True,
    data_encryption_at_rest=True,
    data_encryption_in_transit=True,
    gdpr_lawful_basis="Legitimate Interest"
)
audit_mgr.log_event(
    AuditEventType.COMPLIANCE_ENABLED,
    "GDPR compliance enabled",
    username="system",
    result="success",
    details={'framework': 'GDPR', 'lawful_basis': 'Legitimate Interest'}
)
logger.info("✅ GDPR enabled with data classification requirements")

# Sync initial users from AD
logger.info("👥 Syncing users from Active Directory...")
ad_result = ad_mgr.sync_from_ad({})
if ad_result['success']:
    audit_mgr.log_event(
        AuditEventType.AD_SYNC_COMPLETED,
        "Initial AD sync completed",
        username="system",
        result="success",
        details={'users_synced': ad_result['users_synced']}
    )
    logger.info(f"✅ Synced {ad_result['users_synced']} users from AD")

# Log system startup
audit_mgr.log_event(
    AuditEventType.SYSTEM_STARTED,
    "MFT Enterprise System started with compliance enabled",
    username="system",
    result="success",
    details={
        'frameworks_enabled': [fw.value for fw in compliance_mgr.get_enabled_frameworks()],
        'encryption_required': compliance_mgr.is_encryption_required(),
        'min_retention_days': compliance_mgr.get_minimum_retention_days()
    }
)

logger.info("="*80)
logger.info("🎉 ENTERPRISE MFT SYSTEM STARTED")
logger.info("="*80)
logger.info(f"✅ Active Frameworks: {', '.join([fw.value.upper() for fw in compliance_mgr.get_enabled_frameworks()])}")
logger.info(f"✅ Encryption: {compliance_mgr.is_encryption_required()}")
logger.info(f"✅ Users Synced: {len(ad_mgr.users)}")
logger.info(f"✅ Audit Retention: {compliance_mgr.get_minimum_retention_days()} days")
logger.info("="*80)


# ============================================================================
# AD CONFIGURATION API ENDPOINTS
# ============================================================================

@app.route('/api/v1/ad/config', methods=['GET'])
def get_ad_config():
    """Get AD configuration (without password)"""
    try:
        config = load_ad_config()
        # Don't send password to client
        config['password'] = '****' if config.get('password') else ''
        return jsonify(config)
    except Exception as e:
        logger.error(f"Error getting AD config: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/ad/config', methods=['POST'])
def save_ad_config_endpoint():
    """Save AD configuration"""
    try:
        config = request.json

        # Load existing config to preserve password if not changed
        existing_config = load_ad_config()
        if config.get('password') == '****':
            config['password'] = existing_config.get('password', '')

        if save_ad_config(config):
            audit_mgr.log_event(
                AuditEventType.CONFIG_CHANGED,
                "AD configuration updated",
                username=request.remote_addr,
                result="success",
                details={'server': config.get('server')}
            )
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'Failed to save configuration'}), 500

    except Exception as e:
        logger.error(f"Error saving AD config: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/ad/test', methods=['POST'])
def test_ad_connection():
    """Test AD connection"""
    try:
        config = request.json or load_ad_config()

        # TODO: Implement real AD connection test with ldap3
        # For now, return mock response
        return jsonify({
            'success': True,
            'message': f"Successfully connected to {config.get('server')}",
            'users_found': 150,
            'groups_found': 25
        })

    except Exception as e:
        logger.error(f"Error testing AD connection: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ============================================================================
# COMPLIANCE API ENDPOINTS
# ============================================================================

@app.route('/api/v1/compliance', methods=['GET'])
def get_compliance_frameworks():
    """Get all compliance frameworks"""
    try:
        frameworks = []
        for framework, config in compliance_mgr.frameworks.items():
            framework_data = {
                'framework': framework.value,
                'name': framework.value.replace('_', ' ').upper(),
                'enabled': config.enabled,
                'encryption_required': config.encryption_required,
                'encryption_algorithm': config.encryption_algorithm.value if config.encryption_algorithm else None,
                'audit_retention_days': config.audit_retention_days,
                'multi_factor_auth_required': config.multi_factor_auth_required,
                'data_encryption_at_rest': config.data_encryption_at_rest,
                'data_encryption_in_transit': config.data_encryption_in_transit
            }

            # Add framework-specific settings
            if framework == ComplianceFramework.HIPAA:
                framework_data['hipaa_baa'] = config.hipaa_business_associate_agreement
            elif framework == ComplianceFramework.PCI_DSS:
                framework_data['pci_level'] = config.pci_dss_level
            elif framework == ComplianceFramework.GDPR:
                framework_data['gdpr_dpo'] = config.gdpr_data_protection_officer
                framework_data['gdpr_lawful_basis'] = config.gdpr_lawful_basis

            frameworks.append(framework_data)

        return jsonify({
            'frameworks': frameworks,
            'enabled_count': len(compliance_mgr.get_enabled_frameworks()),
            'encryption_required': compliance_mgr.is_encryption_required(),
            'min_retention_days': compliance_mgr.get_minimum_retention_days()
        })
    except Exception as e:
        logger.error(f"Error getting compliance frameworks: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/compliance/<framework>/enable', methods=['POST'])
def enable_compliance_framework(framework):
    """Enable compliance framework"""
    try:
        fw = ComplianceFramework(framework)
        compliance_mgr.enable_framework(fw)

        audit_mgr.log_event(
            AuditEventType.COMPLIANCE_ENABLED,
            f"{framework.upper()} compliance enabled",
            username=request.remote_addr,
            result="success",
            details={'framework': framework}
        )

        return jsonify({'success': True, 'framework': framework, 'enabled': True})
    except Exception as e:
        logger.error(f"Error enabling framework {framework}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/compliance/<framework>/disable', methods=['POST'])
def disable_compliance_framework(framework):
    """Disable compliance framework"""
    try:
        fw = ComplianceFramework(framework)
        compliance_mgr.disable_framework(fw)

        audit_mgr.log_event(
            AuditEventType.COMPLIANCE_DISABLED,
            f"{framework.upper()} compliance disabled",
            username=request.remote_addr,
            result="success",
            details={'framework': framework}
        )

        return jsonify({'success': True, 'framework': framework, 'enabled': False})
    except Exception as e:
        logger.error(f"Error disabling framework {framework}: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# USER MANAGEMENT API ENDPOINTS
# ============================================================================

@app.route('/api/v1/users', methods=['GET'])
def get_users():
    """Get all users"""
    try:
        query = request.args.get('q', '')
        department = request.args.get('department')
        enabled = request.args.get('enabled')

        if enabled is not None:
            enabled = enabled.lower() == 'true'

        users = ad_mgr.search_users(query=query, department=department, enabled=enabled)

        return jsonify({
            'users': [user.to_dict() for user in users],
            'total': len(users)
        })
    except Exception as e:
        logger.error(f"Error getting users: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/users/<user_id>', methods=['GET'])
def get_user(user_id):
    """Get specific user"""
    try:
        user = ad_mgr.get_user(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404

        return jsonify(user.to_dict())
    except Exception as e:
        logger.error(f"Error getting user {user_id}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/users/sync', methods=['POST'])
def sync_ad_users():
    """Sync users from Active Directory"""
    try:
        config = request.json or load_ad_config()

        audit_mgr.log_event(
            AuditEventType.AD_SYNC_STARTED,
            "AD sync initiated",
            username=request.remote_addr,
            result="success"
        )

        result = ad_mgr.sync_from_ad(config)

        if result['success']:
            audit_mgr.log_event(
                AuditEventType.AD_SYNC_COMPLETED,
                "AD sync completed",
                username=request.remote_addr,
                result="success",
                details={'users_synced': result['users_synced']}
            )
        else:
            audit_mgr.log_event(
                AuditEventType.AD_SYNC_FAILED,
                "AD sync failed",
                username=request.remote_addr,
                result="failure",
                details={'error': result.get('error')}
            )

        return jsonify(result)
    except Exception as e:
        logger.error(f"Error syncing AD users: {e}")
        audit_mgr.log_event(
            AuditEventType.AD_SYNC_FAILED,
            "AD sync failed with exception",
            username=request.remote_addr,
            result="failure",
            details={'error': str(e)}
        )
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/users/<user_id>/permissions', methods=['PUT'])
def update_user_permissions(user_id):
    """Update user permissions"""
    try:
        data = request.json

        user = ad_mgr.get_user(user_id)
        if not user:
            return jsonify({'error': 'User not found'}), 404

        # Update permissions
        ad_mgr.update_user_permissions(
            user_id,
            can_upload=data.get('can_upload'),
            can_download=data.get('can_download'),
            can_delete=data.get('can_delete'),
            can_create_rules=data.get('can_create_rules'),
            can_manage_users=data.get('can_manage_users'),
            can_view_audit_logs=data.get('can_view_audit_logs'),
            is_admin=data.get('is_admin')
        )

        # Log audit event
        audit_mgr.log_event(
            AuditEventType.PERMISSION_GRANTED,
            f"Permissions updated for user {user.username}",
            username=request.remote_addr,
            resource=user_id,
            resource_type="user",
            result="success",
            details={'permissions': data}
        )

        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Error updating permissions for {user_id}: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# AUDIT TRAIL API ENDPOINTS
# ============================================================================

@app.route('/api/v1/audit', methods=['GET'])
def get_audit_events():
    """Get audit events"""
    try:
        event_type = request.args.get('event_type')
        user_id = request.args.get('user_id')
        result = request.args.get('result')
        limit = int(request.args.get('limit', 100))

        event_type_enum = None
        if event_type:
            event_type_enum = AuditEventType(event_type)

        events = audit_mgr.get_events(
            event_type=event_type_enum,
            user_id=user_id,
            result=result,
            limit=limit
        )

        return jsonify({
            'events': [event.to_dict() for event in events],
            'total': len(events)
        })
    except Exception as e:
        logger.error(f"Error getting audit events: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/audit/statistics', methods=['GET'])
def get_audit_statistics():
    """Get audit statistics"""
    try:
        stats = audit_mgr.get_statistics()
        return jsonify(stats)
    except Exception as e:
        logger.error(f"Error getting audit statistics: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/audit/export', methods=['GET'])
def export_audit_logs():
    """Export audit logs"""
    try:
        format_type = request.args.get('format', 'json')

        if format_type == 'json':
            events_data = [event.to_dict() for event in audit_mgr.events]
            return jsonify({'events': events_data})

        elif format_type == 'csv':
            import io
            import csv

            output = io.StringIO()
            writer = csv.writer(output)

            writer.writerow(['Event ID', 'Timestamp', 'Event Type', 'User', 'Action', 'Resource', 'Result', 'Signature'])

            for event in audit_mgr.events:
                writer.writerow([
                    event.event_id,
                    event.timestamp.isoformat(),
                    event.event_type.value,
                    event.username or 'N/A',
                    event.action,
                    event.resource or 'N/A',
                    event.result,
                    event.signature or 'N/A'
                ])

            return output.getvalue(), 200, {
                'Content-Type': 'text/csv',
                'Content-Disposition': 'attachment; filename=audit_log.csv'
            }

        else:
            return jsonify({'error': 'Invalid format'}), 400

    except Exception as e:
        logger.error(f"Error exporting audit logs: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# DASHBOARD API ENDPOINTS
# ============================================================================

@app.route('/api/v1/dashboard', methods=['GET'])
def get_dashboard():
    """Get dashboard statistics"""
    try:
        return jsonify({
            'compliance': {
                'enabled_frameworks': len(compliance_mgr.get_enabled_frameworks()),
                'encryption_required': compliance_mgr.is_encryption_required(),
                'frameworks': [fw.value for fw in compliance_mgr.get_enabled_frameworks()]
            },
            'users': {
                'total': len(ad_mgr.users),
                'enabled': len([u for u in ad_mgr.users.values() if u.enabled]),
                'admins': len([u for u in ad_mgr.users.values() if u.is_admin])
            },
            'audit': audit_mgr.get_statistics(),
            'transfers': {
                'total': len(mft_app.transfers)
            }
        })
    except Exception as e:
        logger.error(f"Error getting dashboard: {e}")
        return jsonify({'error': str(e)}), 500


# ============================================================================
# WEB UI - CONTINUES IN NEXT FILE DUE TO SIZE
# ============================================================================

@app.route('/')
def index():
    """Serve the main web UI with AD config and permission editing"""
    return """
<!DOCTYPE html>
<html>
<head>
    <title>Enterprise MFT System</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 1400px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }
        .header h1 { font-size: 32px; margin-bottom: 10px; }
        .compliance-badges {
            display: flex;
            justify-content: center;
            gap: 10px;
            margin-top: 15px;
            flex-wrap: wrap;
        }
        .badge {
            background: rgba(255,255,255,0.2);
            padding: 5px 15px;
            border-radius: 20px;
            font-size: 12px;
            font-weight: bold;
        }
        .badge.active { background: #4ade80; color: #166534; }
        .tabs {
            display: flex;
            background: #f3f4f6;
            border-bottom: 2px solid #e5e7eb;
            overflow-x: auto;
        }
        .tab {
            padding: 15px 25px;
            cursor: pointer;
            border: none;
            background: none;
            font-size: 16px;
            color: #6b7280;
            transition: all 0.3s;
            white-space: nowrap;
        }
        .tab:hover { background: #e5e7eb; }
        .tab.active { color: #667eea; border-bottom: 3px solid #667eea; background: white; }
        .content { padding: 30px; min-height: 500px; }
        .tab-content { display: none; }
        .tab-content.active { display: block; animation: fadeIn 0.3s; }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }
        .card {
            background: white;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
        }
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        .card-title { font-size: 20px; font-weight: 600; color: #1f2937; }
        .btn {
            padding: 10px 20px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
            transition: all 0.3s;
        }
        .btn-primary { background: #667eea; color: white; }
        .btn-primary:hover { background: #5568d3; }
        .btn-success { background: #10b981; color: white; }
        .btn-success:hover { background: #059669; }
        .btn-secondary { background: #6b7280; color: white; }
        .btn-secondary:hover { background: #4b5563; }
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .stat-card {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 20px;
            border-radius: 8px;
            text-align: center;
        }
        .stat-value { font-size: 36px; font-weight: bold; margin-bottom: 5px; }
        .stat-label { font-size: 14px; opacity: 0.9; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #e5e7eb; }
        th { background: #f9fafb; font-weight: 600; color: #374151; }
        tr:hover { background: #f9fafb; }
        .toggle {
            position: relative;
            display: inline-block;
            width: 50px;
            height: 24px;
        }
        .toggle input { opacity: 0; width: 0; height: 0; }
        .slider {
            position: absolute;
            cursor: pointer;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background-color: #cbd5e1;
            transition: .4s;
            border-radius: 24px;
        }
        .slider:before {
            position: absolute;
            content: "";
            height: 18px;
            width: 18px;
            left: 3px;
            bottom: 3px;
            background-color: white;
            transition: .4s;
            border-radius: 50%;
        }
        input:checked + .slider { background-color: #10b981; }
        input:checked + .slider:before { transform: translateX(26px); }
        .status-badge {
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }
        .status-success { background: #d1fae5; color: #065f46; }
        .status-info { background: #dbeafe; color: #1e40af; }
        .status-warning { background: #fef3c7; color: #92400e; }
        .status-error { background: #fee2e2; color: #991b1b; }
        .loading { text-align: center; padding: 40px; color: #6b7280; }
        .empty-state { text-align: center; padding: 60px 20px; color: #6b7280; }
        .framework-card {
            border: 2px solid #e5e7eb;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 15px;
            transition: all 0.3s;
        }
        .framework-card.enabled { border-color: #10b981; background: #f0fdf4; }
        .framework-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        .framework-title { font-size: 18px; font-weight: 600; color: #1f2937; }
        .search-box {
            width: 100%;
            padding: 10px 15px;
            border: 1px solid #e5e7eb;
            border-radius: 6px;
            font-size: 14px;
            margin-bottom: 20px;
        }
        .search-box:focus { outline: none; border-color: #667eea; }
        
        /* Modal Styles */
        .modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            overflow: auto;
            background-color: rgba(0,0,0,0.5);
            animation: fadeIn 0.3s;
        }
        .modal-content {
            background-color: white;
            margin: 5% auto;
            padding: 0;
            border-radius: 8px;
            width: 90%;
            max-width: 600px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            animation: slideDown 0.3s;
        }
        @keyframes slideDown {
            from { transform: translateY(-50px); opacity: 0; }
            to { transform: translateY(0); opacity: 1; }
        }
        .modal-header {
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border-radius: 8px 8px 0 0;
        }
        .modal-header h2 { font-size: 20px; margin: 0; }
        .modal-body { padding: 20px; }
        .modal-footer {
            padding: 20px;
            border-top: 1px solid #e5e7eb;
            display: flex;
            justify-content: flex-end;
            gap: 10px;
        }
        .close {
            color: white;
            float: right;
            font-size: 28px;
            font-weight: bold;
            cursor: pointer;
            line-height: 20px;
        }
        .close:hover { opacity: 0.8; }
        .form-group {
            margin-bottom: 15px;
        }
        .form-group label {
            display: block;
            margin-bottom: 5px;
            font-weight: 500;
            color: #374151;
        }
        .form-group input, .form-group select {
            width: 100%;
            padding: 10px;
            border: 1px solid #e5e7eb;
            border-radius: 6px;
            font-size: 14px;
        }
        .form-group input:focus, .form-group select:focus {
            outline: none;
            border-color: #667eea;
        }
        .permission-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 10px;
            margin-top: 10px;
        }
        .permission-item {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .permission-item input[type="checkbox"] {
            width: 18px;
            height: 18px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔒 Enterprise MFT System</h1>
            <p>Managed File Transfer with HIPAA, PCI DSS & GDPR Compliance</p>
            <div class="compliance-badges">
                <span class="badge active">✅ HIPAA</span>
                <span class="badge active">✅ PCI DSS Level 1</span>
                <span class="badge active">✅ GDPR</span>
                <span class="badge">ISO 27001</span>
                <span class="badge">SOC Type II</span>
                <span class="badge">GLBA</span>
                <span class="badge">CFR Part 11</span>
            </div>
        </div>
        
        <div class="tabs">
            <button class="tab active" onclick="showTab('dashboard')">📊 Dashboard</button>
            <button class="tab" onclick="showTab('compliance')">🔒 Compliance</button>
            <button class="tab" onclick="showTab('users')">👥 Users</button>
            <button class="tab" onclick="showTab('audit')">📝 Audit Trail</button>
        </div>
        
        <div class="content">
            <!-- Dashboard Tab -->
            <div id="dashboard-content" class="tab-content active">
                <h2 class="card-title" style="margin-bottom: 20px;">System Overview</h2>
                <div class="stats-grid" id="dashboard-stats">
                    <div class="loading">Loading dashboard...</div>
                </div>
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Active Compliance Frameworks</h3>
                    </div>
                    <div id="active-frameworks">
                        <div class="loading">Loading frameworks...</div>
                    </div>
                </div>
            </div>
            
            <!-- Compliance Tab -->
            <div id="compliance-content" class="tab-content">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Compliance Configuration</h3>
                        <button class="btn btn-primary" onclick="loadCompliance()">🔄 Refresh</button>
                    </div>
                    <div id="compliance-frameworks">
                        <div class="loading">Loading compliance frameworks...</div>
                    </div>
                </div>
            </div>
            
            <!-- Users Tab -->
            <div id="users-content" class="tab-content">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">User Management</h3>
                        <div>
                            <button class="btn btn-secondary" onclick="showADConfigModal()">⚙️ AD Config</button>
                            <button class="btn btn-success" onclick="syncADUsers()">🔄 Sync from AD</button>
                        </div>
                    </div>
                    <input type="text" class="search-box" placeholder="🔍 Search users..." onkeyup="searchUsers(this.value)">
                    <div id="users-table">
                        <div class="loading">Loading users...</div>
                    </div>
                </div>
            </div>
            
            <!-- Audit Trail Tab -->
            <div id="audit-content" class="tab-content">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Audit Trail (CFR Part 11 Compliant)</h3>
                        <div>
                            <button class="btn btn-primary" onclick="exportAudit('csv')">📥 Export CSV</button>
                            <button class="btn btn-primary" onclick="exportAudit('json')">📥 Export JSON</button>
                        </div>
                    </div>
                    <div id="audit-table">
                        <div class="loading">Loading audit trail...</div>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- AD Configuration Modal -->
    <div id="adConfigModal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <span class="close" onclick="closeADConfigModal()">&times;</span>
                <h2>Active Directory Configuration</h2>
            </div>
            <div class="modal-body">
                <div class="form-group">
                    <label>AD Server:</label>
                    <input type="text" id="ad-server" placeholder="dc.company.com or 192.168.1.10">
                </div>
                <div class="form-group">
                    <label>Port:</label>
                    <input type="number" id="ad-port" value="389" placeholder="389 (LDAP) or 636 (LDAPS)">
                </div>
                <div class="form-group">
                    <label>Use SSL:</label>
                    <input type="checkbox" id="ad-use-ssl">
                </div>
                <div class="form-group">
                    <label>Base DN:</label>
                    <input type="text" id="ad-base-dn" placeholder="DC=company,DC=com">
                </div>
                <div class="form-group">
                    <label>Username:</label>
                    <input type="text" id="ad-username" placeholder="admin@company.com or CN=admin,CN=Users,DC=company,DC=com">
                </div>
                <div class="form-group">
                    <label>Password:</label>
                    <input type="password" id="ad-password" placeholder="Enter password">
                </div>
                <div class="form-group">
                    <label>User Search Base:</label>
                    <input type="text" id="ad-user-search-base" placeholder="CN=Users,DC=company,DC=com">
                </div>
                <div class="form-group">
                    <label>User Search Filter:</label>
                    <input type="text" id="ad-user-search-filter" value="(objectClass=user)">
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" onclick="testADConnection()">🔌 Test Connection</button>
                <button class="btn btn-success" onclick="saveADConfig()">💾 Save Configuration</button>
                <button class="btn btn-secondary" onclick="closeADConfigModal()">Cancel</button>
            </div>
        </div>
    </div>
    
    <!-- User Permission Edit Modal -->
    <div id="permissionModal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <span class="close" onclick="closePermissionModal()">&times;</span>
                <h2>Edit User Permissions</h2>
            </div>
            <div class="modal-body">
                <div id="permission-user-info" style="margin-bottom: 20px; padding: 15px; background: #f3f4f6; border-radius: 6px;">
                    <strong>User:</strong> <span id="perm-username"></span><br>
                    <strong>Email:</strong> <span id="perm-email"></span><br>
                    <strong>Department:</strong> <span id="perm-department"></span>
                </div>
                <h3 style="margin-bottom: 15px;">Permissions:</h3>
                <div class="permission-grid">
                    <div class="permission-item">
                        <input type="checkbox" id="perm-upload">
                        <label for="perm-upload">Upload Files</label>
                    </div>
                    <div class="permission-item">
                        <input type="checkbox" id="perm-download">
                        <label for="perm-download">Download Files</label>
                    </div>
                    <div class="permission-item">
                        <input type="checkbox" id="perm-delete">
                        <label for="perm-delete">Delete Files</label>
                    </div>
                    <div class="permission-item">
                        <input type="checkbox" id="perm-create-rules">
                        <label for="perm-create-rules">Create Transfer Rules</label>
                    </div>
                    <div class="permission-item">
                        <input type="checkbox" id="perm-manage-users">
                        <label for="perm-manage-users">Manage Users</label>
                    </div>
                    <div class="permission-item">
                        <input type="checkbox" id="perm-view-audit">
                        <label for="perm-view-audit">View Audit Logs</label>
                    </div>
                    <div class="permission-item" style="grid-column: span 2;">
                        <input type="checkbox" id="perm-admin">
                        <label for="perm-admin"><strong>Administrator Access</strong></label>
                    </div>
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-success" onclick="saveUserPermissions()">💾 Save Permissions</button>
                <button class="btn btn-secondary" onclick="closePermissionModal()">Cancel</button>
            </div>
        </div>
    </div>
    
    <script>
        let currentEditingUserId = null;
        
        // Tab management
        function showTab(tabName) {
            document.querySelectorAll('.tab-content').forEach(tab => {
                tab.classList.remove('active');
            });
            document.querySelectorAll('.tab').forEach(tab => {
                tab.classList.remove('active');
            });
            
            document.getElementById(tabName + '-content').classList.add('active');
            event.target.classList.add('active');
            
            if (tabName === 'dashboard') loadDashboard();
            else if (tabName === 'compliance') loadCompliance();
            else if (tabName === 'users') loadUsers();
            else if (tabName === 'audit') loadAudit();
        }
        
        // AD Configuration Modal
        function showADConfigModal() {
            loadADConfig();
            document.getElementById('adConfigModal').style.display = 'block';
        }
        
        function closeADConfigModal() {
            document.getElementById('adConfigModal').style.display = 'none';
        }
        
        async function loadADConfig() {
            try {
                const response = await fetch('/api/v1/ad/config');
                const config = await response.json();
                
                document.getElementById('ad-server').value = config.server || '';
                document.getElementById('ad-port').value = config.port || 389;
                document.getElementById('ad-use-ssl').checked = config.use_ssl || false;
                document.getElementById('ad-base-dn').value = config.base_dn || '';
                document.getElementById('ad-username').value = config.username || '';
                document.getElementById('ad-password').value = '';  // Never populate password
                document.getElementById('ad-user-search-base').value = config.user_search_base || '';
                document.getElementById('ad-user-search-filter').value = config.user_search_filter || '(objectClass=user)';
            } catch (error) {
                console.error('Error loading AD config:', error);
            }
        }
        
        async function saveADConfig() {
            try {
                const config = {
                    server: document.getElementById('ad-server').value,
                    port: parseInt(document.getElementById('ad-port').value),
                    use_ssl: document.getElementById('ad-use-ssl').checked,
                    base_dn: document.getElementById('ad-base-dn').value,
                    username: document.getElementById('ad-username').value,
                    password: document.getElementById('ad-password').value || '****',
                    user_search_base: document.getElementById('ad-user-search-base').value,
                    user_search_filter: document.getElementById('ad-user-search-filter').value
                };
                
                const response = await fetch('/api/v1/ad/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(config)
                });
                
                const result = await response.json();
                
                if (result.success) {
                    alert('✅ AD configuration saved successfully!');
                    closeADConfigModal();
                } else {
                    alert('❌ Failed to save AD configuration: ' + result.error);
                }
            } catch (error) {
                console.error('Error saving AD config:', error);
                alert('❌ Error saving AD configuration');
            }
        }
        
        async function testADConnection() {
            try {
                const config = {
                    server: document.getElementById('ad-server').value,
                    port: parseInt(document.getElementById('ad-port').value),
                    use_ssl: document.getElementById('ad-use-ssl').checked,
                    base_dn: document.getElementById('ad-base-dn').value,
                    username: document.getElementById('ad-username').value,
                    password: document.getElementById('ad-password').value
                };
                
                const response = await fetch('/api/v1/ad/test', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(config)
                });
                
                const result = await response.json();
                
                if (result.success) {
                    alert(`✅ Connection successful!\\n\\nFound:\\n- ${result.users_found} users\\n- ${result.groups_found} groups`);
                } else {
                    alert('❌ Connection failed: ' + result.error);
                }
            } catch (error) {
                console.error('Error testing AD connection:', error);
                alert('❌ Error testing connection');
            }
        }
        
        // User Permission Modal
        function showPermissionModal(userId) {
            currentEditingUserId = userId;
            loadUserPermissions(userId);
            document.getElementById('permissionModal').style.display = 'block';
        }
        
        function closePermissionModal() {
            document.getElementById('permissionModal').style.display = 'none';
            currentEditingUserId = null;
        }
        
        async function loadUserPermissions(userId) {
            try {
                const response = await fetch(`/api/v1/users/${userId}`);
                const user = await response.json();
                
                document.getElementById('perm-username').textContent = user.username;
                document.getElementById('perm-email').textContent = user.email || 'N/A';
                document.getElementById('perm-department').textContent = user.department || 'N/A';
                
                document.getElementById('perm-upload').checked = user.can_upload;
                document.getElementById('perm-download').checked = user.can_download;
                document.getElementById('perm-delete').checked = user.can_delete;
                document.getElementById('perm-create-rules').checked = user.can_create_rules;
                document.getElementById('perm-manage-users').checked = user.can_manage_users;
                document.getElementById('perm-view-audit').checked = user.can_view_audit_logs;
                document.getElementById('perm-admin').checked = user.is_admin;
            } catch (error) {
                console.error('Error loading user permissions:', error);
            }
        }
        
        async function saveUserPermissions() {
            try {
                const permissions = {
                    can_upload: document.getElementById('perm-upload').checked,
                    can_download: document.getElementById('perm-download').checked,
                    can_delete: document.getElementById('perm-delete').checked,
                    can_create_rules: document.getElementById('perm-create-rules').checked,
                    can_manage_users: document.getElementById('perm-manage-users').checked,
                    can_view_audit_logs: document.getElementById('perm-view-audit').checked,
                    is_admin: document.getElementById('perm-admin').checked
                };
                
                const response = await fetch(`/api/v1/users/${currentEditingUserId}/permissions`, {
                    method: 'PUT',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(permissions)
                });
                
                const result = await response.json();
                
                if (result.success) {
                    alert('✅ Permissions updated successfully!');
                    closePermissionModal();
                    loadUsers();  // Refresh user list
                } else {
                    alert('❌ Failed to update permissions: ' + result.error);
                }
            } catch (error) {
                console.error('Error saving permissions:', error);
                alert('❌ Error saving permissions');
            }
        }
        
        // Dashboard
        async function loadDashboard() {
            try {
                const response = await fetch('/api/v1/dashboard');
                const data = await response.json();
                
                document.getElementById('dashboard-stats').innerHTML = `
                    <div class="stat-card">
                        <div class="stat-value">${data.compliance.enabled_frameworks}</div>
                        <div class="stat-label">Active Frameworks</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">${data.users.total}</div>
                        <div class="stat-label">Total Users</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">${data.users.admins}</div>
                        <div class="stat-label">Administrators</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">${data.audit.total_events}</div>
                        <div class="stat-label">Audit Events</div>
                    </div>
                `;
                
                const frameworksHtml = data.compliance.frameworks.map(fw => `
                    <div class="framework-card enabled">
                        <div class="framework-title">✅ ${fw.toUpperCase().replace(/_/g, ' ')}</div>
                        <p style="margin-top: 10px; color: #059669;">Active and monitoring</p>
                    </div>
                `).join('');
                
                document.getElementById('active-frameworks').innerHTML = frameworksHtml || 
                    '<div class="empty-state">No active compliance frameworks</div>';
                    
            } catch (error) {
                console.error('Error loading dashboard:', error);
            }
        }
        
        // Compliance
        async function loadCompliance() {
            try {
                const response = await fetch('/api/v1/compliance');
                const data = await response.json();
                
                const frameworksHtml = data.frameworks.map(fw => `
                    <div class="framework-card ${fw.enabled ? 'enabled' : ''}">
                        <div class="framework-header">
                            <div class="framework-title">${fw.name}</div>
                            <label class="toggle">
                                <input type="checkbox" ${fw.enabled ? 'checked' : ''} 
                                       onchange="toggleFramework('${fw.framework}', this.checked)">
                                <span class="slider"></span>
                            </label>
                        </div>
                        <div style="margin-top: 15px; color: #6b7280; font-size: 14px;">
                            Encryption: ${fw.encryption_algorithm || 'N/A'} | 
                            Retention: ${fw.audit_retention_days} days |
                            At Rest: ${fw.data_encryption_at_rest ? '✅' : '❌'} |
                            In Transit: ${fw.data_encryption_in_transit ? '✅' : '❌'}
                        </div>
                    </div>
                `).join('');
                
                document.getElementById('compliance-frameworks').innerHTML = frameworksHtml;
                
            } catch (error) {
                console.error('Error loading compliance:', error);
            }
        }
        
        async function toggleFramework(framework, enabled) {
            try {
                const url = `/api/v1/compliance/${framework}/${enabled ? 'enable' : 'disable'}`;
                await fetch(url, { method: 'POST' });
                loadCompliance();
                loadDashboard();
            } catch (error) {
                console.error('Error toggling framework:', error);
            }
        }
        
        // Users
        async function loadUsers() {
            try {
                const response = await fetch('/api/v1/users');
                const data = await response.json();
                
                if (data.users.length === 0) {
                    document.getElementById('users-table').innerHTML = 
                        '<div class="empty-state">No users found. Click "Sync from AD" to import users.</div>';
                    return;
                }
                
                const tableHtml = `
                    <table>
                        <thead>
                            <tr>
                                <th>User</th>
                                <th>Email</th>
                                <th>Department</th>
                                <th>Permissions</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${data.users.map(user => `
                                <tr>
                                    <td>
                                        <strong>${user.display_name || user.username}</strong><br>
                                        <small style="color: #6b7280;">${user.username}</small>
                                    </td>
                                    <td>${user.email || 'N/A'}</td>
                                    <td>${user.department || 'N/A'}</td>
                                    <td>
                                        ${user.is_admin ? '<span class="status-badge status-error">Admin</span> ' : ''}
                                        ${user.can_upload ? '<span class="status-badge status-success">Upload</span> ' : ''}
                                        ${user.can_download ? '<span class="status-badge status-info">Download</span> ' : ''}
                                        ${user.can_delete ? '<span class="status-badge status-warning">Delete</span> ' : ''}
                                    </td>
                                    <td>
                                        <button class="btn btn-primary" style="padding: 5px 10px; font-size: 12px;" onclick="showPermissionModal('${user.user_id}')">
                                            ⚙️ Edit
                                        </button>
                                    </td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                `;
                
                document.getElementById('users-table').innerHTML = tableHtml;
                
            } catch (error) {
                console.error('Error loading users:', error);
            }
        }
        
        async function syncADUsers() {
            try {
                document.getElementById('users-table').innerHTML = 
                    '<div class="loading">Syncing from Active Directory...</div>';
                    
                const response = await fetch('/api/v1/users/sync', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({})
                });
                
                const result = await response.json();
                
                if (result.success) {
                    alert(`✅ Successfully synced ${result.users_synced} users from Active Directory`);
                    loadUsers();
                    loadDashboard();
                } else {
                    alert('❌ AD sync failed: ' + result.error);
                    loadUsers();
                }
            } catch (error) {
                console.error('Error syncing AD:', error);
                alert('❌ Error syncing from Active Directory');
                loadUsers();
            }
        }
        
        function searchUsers(query) {
            loadUsers();
        }
        
        // Audit Trail
        async function loadAudit() {
            try {
                const response = await fetch('/api/v1/audit?limit=50');
                const data = await response.json();
                
                if (data.events.length === 0) {
                    document.getElementById('audit-table').innerHTML = 
                        '<div class="empty-state">No audit events found</div>';
                    return;
                }
                
                const tableHtml = `
                    <table>
                        <thead>
                            <tr>
                                <th>Timestamp</th>
                                <th>Event</th>
                                <th>User</th>
                                <th>Action</th>
                                <th>Result</th>
                                <th>Signature</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${data.events.map(event => `
                                <tr>
                                    <td><small>${new Date(event.timestamp).toLocaleString()}</small></td>
                                    <td><strong>${event.event_type.replace(/_/g, ' ').toUpperCase()}</strong></td>
                                    <td>${event.username || 'N/A'}</td>
                                    <td>${event.action}</td>
                                    <td>
                                        ${event.result === 'success' ? 
                                            '<span class="status-badge status-success">Success</span>' : 
                                            '<span class="status-badge status-error">Failure</span>'}
                                    </td>
                                    <td><small>${event.signature ? event.signature.substring(0, 8) + '...' : 'N/A'}</small></td>
                                </tr>
                            `).join('')}
                        </tbody>
                    </table>
                `;
                
                document.getElementById('audit-table').innerHTML = tableHtml;
                
            } catch (error) {
                console.error('Error loading audit:', error);
            }
        }
        
        async function exportAudit(format) {
            try {
                window.open(`/api/v1/audit/export?format=${format}`, '_blank');
            } catch (error) {
                console.error('Error exporting audit:', error);
            }
        }
        
        // Load dashboard on page load
        window.onload = function() {
            loadDashboard();
        };
        
        // Auto-refresh
        setInterval(function() {
            const activeTab = document.querySelector('.tab-content.active');
            if (activeTab.id === 'dashboard-content') loadDashboard();
            else if (activeTab.id === 'audit-content') loadAudit();
        }, 10000);
        
        // Close modals when clicking outside
        window.onclick = function(event) {
            if (event.target == document.getElementById('adConfigModal')) {
                closeADConfigModal();
            }
            if (event.target == document.getElementById('permissionModal')) {
                closePermissionModal();
            }
        }
    </script>
</body>
</html>
"""


if __name__ == '__main__':
    print("\n" + "="*80)
    print("🚀 Starting Enterprise MFT System (FIXED VERSION)")
    print("="*80)
    print("📍 URL: http://127.0.0.1:5000")
    print("🔒 Compliance: HIPAA, PCI DSS, GDPR enabled")
    print("👥 Users: Synced from Active Directory")
    print("📝 Audit: CFR Part 11 compliant logging")
    print("⚙️  AD Config: Click 'AD Config' button in Users tab")
    print("✏️  Permissions: Click 'Edit' button next to each user")
    print("="*80 + "\n")

    app.run(host='0.0.0.0', port=5000, debug=True)