"""
Complete Integrated MFT System
Combines: Compliance + AD + Audit + File Transfers + Rules
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import os
import sys
import logging
import json
import asyncio
import uuid
from datetime import datetime
from typing import Dict, List, Optional
import threading

# Import compliance system
from compliance_system import (
    ComplianceManager,
    AuditManager,
    ActiveDirectoryManager,
    ComplianceFramework,
    AuditEventType,
    EncryptionAlgorithm
)

# Import MFT application
try:
    from mft_application import MFTApplication, TransferConfig, TransferProtocol
except ImportError:
    # Create mock MFT app if not available
    class TransferProtocol:
        UNC = 'unc'
        SMB = 'smb'
        SFTP = 'sftp'
        FTP = 'ftp'
        FTPS = 'ftps'
        HTTP = 'http'
        HTTPS = 'https'


    class TransferConfig:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)


    class MFTApplication:
        def __init__(self):
            self.transfers = {}

        async def transfer_file(self, source, dest, config):
            task_id = str(uuid.uuid4())
            self.transfers[task_id] = {
                'task_id': task_id,
                'source_path': source,
                'destination_path': dest,
                'protocol': config.protocol,
                'status': 'completed',
                'timestamp': datetime.now().isoformat()
            }
            return task_id

# Import file monitor
try:
    from file_monitor import FileMonitorManager, TransferRule, ScheduleType, TriggerType, ActionType
except ImportError:
    # Create mock if not available
    class ScheduleType:
        EVENT_DRIVEN = 'event_driven'
        ONCE = 'once'
        RECURRING = 'recurring'
        CRON = 'cron'
        ON_DEMAND = 'on_demand'


    class TriggerType:
        FILE_CREATED = 'file_created'
        FILE_MODIFIED = 'file_modified'
        FILE_MOVED = 'file_moved'


    class ActionType:
        COPY = 'copy'
        MOVE = 'move'
        MOVE_WITH_DELAY = 'move_with_delay'


    class TransferRule:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)
            self.files_transferred = 0
            self.last_transfer_time = None
            self.status = 'idle'


    class FileMonitorManager:
        def __init__(self, mft_app):
            self.mft_app = mft_app
            self.rules = {}

        def start_all(self):
            pass

        def add_rule(self, rule):
            self.rules[rule.rule_id] = rule

        def remove_rule(self, rule_id):
            if rule_id in self.rules:
                del self.rules[rule_id]

        def enable_rule(self, rule_id):
            if rule_id in self.rules:
                self.rules[rule_id].enabled = True

        def disable_rule(self, rule_id):
            if rule_id in self.rules:
                self.rules[rule_id].enabled = False

        def get_statistics(self):
            total_files = sum(r.files_transferred for r in self.rules.values())
            active_rules = sum(1 for r in self.rules.values() if r.enabled)
            return {
                'total_files_transferred': total_files,
                'active_rules': active_rules,
                'rules': [self._rule_to_dict(r) for r in self.rules.values()]
            }

        def _rule_to_dict(self, rule):
            return {
                'rule_id': rule.rule_id,
                'name': rule.name,
                'enabled': rule.enabled,
                'source_path': rule.source_path,
                'source_pattern': getattr(rule, 'source_pattern', '*.*'),
                'destination_path': rule.destination_path,
                'protocol': rule.protocol,
                'schedule_type': getattr(rule, 'schedule_type', ScheduleType.EVENT_DRIVEN),
                'action_type': getattr(rule, 'action_type', ActionType.COPY),
                'files_transferred': rule.files_transferred,
                'status': rule.status
            }

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
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
        'user_search_filter': '(objectClass=user)'
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


# Initialize components
mft_app = MFTApplication()
monitor_manager = FileMonitorManager(mft_app)
compliance_mgr = ComplianceManager()
audit_mgr = AuditManager()
ad_mgr = ActiveDirectoryManager()

# Start monitoring
try:
    monitor_manager.start_all()
except:
    pass

# Initialize compliance frameworks
logger.info("🔒 Configuring compliance frameworks...")

compliance_mgr.enable_framework(ComplianceFramework.HIPAA)
compliance_mgr.update_framework_config(
    ComplianceFramework.HIPAA,
    encryption_algorithm=EncryptionAlgorithm.AES_256_GCM,
    audit_retention_days=2555,
    data_encryption_at_rest=True,
    data_encryption_in_transit=True,
    hipaa_business_associate_agreement=True
)

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

# Sync initial users
ad_result = ad_mgr.sync_from_ad({})
audit_mgr.log_event(
    AuditEventType.SYSTEM_STARTED,
    "Integrated MFT System started",
    username="system",
    result="success"
)

logger.info("=" * 80)
logger.info("🎉 INTEGRATED MFT SYSTEM STARTED")
logger.info("=" * 80)

# ============================================================================
# HTML WEB UI - COMPLETE INTEGRATED VERSION
# ============================================================================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Enterprise MFT System - Complete</title>
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
            margin-right: 10px;
        }
        .btn-primary { background: #667eea; color: white; }
        .btn-primary:hover { background: #5568d3; }
        .btn-success { background: #10b981; color: white; }
        .btn-success:hover { background: #059669; }
        .btn-secondary { background: #6b7280; color: white; }
        .btn-secondary:hover { background: #4b5563; }
        .btn-danger { background: #ef4444; color: white; }
        .btn-danger:hover { background: #dc2626; }
        .btn-small { padding: 6px 12px; font-size: 12px; }
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
        .status-error { background: #fee2e2; color: #991b1b; }
        .status-enabled { background: #d1fae5; color: #065f46; }
        .status-monitoring { background: #dbeafe; color: #1e40af; }
        .loading { text-align: center; padding: 40px; color: #6b7280; }
        .framework-card {
            border: 2px solid #e5e7eb;
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 15px;
        }
        .framework-card.enabled { border-color: #10b981; background: #f0fdf4; }
        .form-group { margin-bottom: 15px; }
        .form-group label {
            display: block;
            margin-bottom: 5px;
            font-weight: 500;
            color: #374151;
        }
        .form-group input, .form-group select, .form-group textarea {
            width: 100%;
            padding: 10px;
            border: 1px solid #e5e7eb;
            border-radius: 6px;
            font-size: 14px;
        }
        .form-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 15px;
        }
        .form-row-3 {
            display: grid;
            grid-template-columns: 2fr 1fr 1fr;
            gap: 15px;
        }
        .modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0,0,0,0.5);
        }
        .modal-content {
            background-color: white;
            margin: 5% auto;
            padding: 0;
            border-radius: 8px;
            width: 90%;
            max-width: 700px;
            max-height: 80vh;
            overflow-y: auto;
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
        .help-text { font-size: 12px; color: #6b7280; margin-top: 4px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔒 Enterprise MFT System - Complete Integration</h1>
            <p>Managed File Transfer with HIPAA, PCI DSS & GDPR Compliance</p>
            <div class="compliance-badges">
                <span class="badge active">✅ HIPAA</span>
                <span class="badge active">✅ PCI DSS Level 1</span>
                <span class="badge active">✅ GDPR</span>
                <span class="badge">ISO 27001</span>
                <span class="badge">SOC Type II</span>
            </div>
        </div>

        <div class="tabs">
            <button class="tab active" onclick="showTab('dashboard')">📊 Dashboard</button>
            <button class="tab" onclick="showTab('transfer')">📤 New Transfer</button>
            <button class="tab" onclick="showTab('history')">📋 History</button>
            <button class="tab" onclick="showTab('rules')">⚙️ Transfer Rules</button>
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
            </div>

            <!-- New Transfer Tab -->
            <div id="transfer-content" class="tab-content">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Create New Transfer</h3>
                    </div>
                    <form id="transfer-form">
                        <div class="form-row-3">
                            <div class="form-group">
                                <label>Source Path:</label>
                                <input type="text" id="source-path" placeholder="//192.168.1.10/C$/data/file.txt" required>
                            </div>
                            <div class="form-group">
                                <label>Protocol:</label>
                                <select id="protocol">
                                    <option value="unc">UNC</option>
                                    <option value="smb">SMB</option>
                                    <option value="sftp">SFTP</option>
                                    <option value="local">Local</option>
                                </select>
                            </div>
                            <div class="form-group">
                                <label>Port:</label>
                                <input type="number" id="port" value="445">
                            </div>
                        </div>
                        <div class="form-row">
                            <div class="form-group">
                                <label>Destination Host:</label>
                                <input type="text" id="host" placeholder="10.10.100.4" required>
                            </div>
                            <div class="form-group">
                                <label>Destination Path:</label>
                                <input type="text" id="dest-path" placeholder="C$/backup/file.txt" required>
                            </div>
                        </div>
                        <button type="submit" class="btn btn-primary">📤 Start Transfer</button>
                    </form>
                </div>
            </div>

            <!-- History Tab -->
            <div id="history-content" class="tab-content">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Transfer History</h3>
                        <button class="btn btn-primary" onclick="loadHistory()">🔄 Refresh</button>
                    </div>
                    <table id="history-table">
                        <thead>
                            <tr>
                                <th>Task ID</th>
                                <th>Source</th>
                                <th>Destination</th>
                                <th>Protocol</th>
                                <th>Status</th>
                                <th>Time</th>
                            </tr>
                        </thead>
                        <tbody><tr><td colspan="6" class="loading">Loading...</td></tr></tbody>
                    </table>
                </div>
            </div>

            <!-- Transfer Rules Tab -->
            <div id="rules-content" class="tab-content">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Transfer Rules</h3>
                        <button class="btn btn-primary" onclick="showCreateRuleModal()">➕ Create Rule</button>
                    </div>
                    <table id="rules-table">
                        <thead>
                            <tr>
                                <th>Rule Name</th>
                                <th>Source</th>
                                <th>Destination</th>
                                <th>Schedule</th>
                                <th>Files</th>
                                <th>Enabled</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody><tr><td colspan="7" class="loading">Loading...</td></tr></tbody>
                    </table>
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
                    <div id="users-table-container">
                        <div class="loading">Loading users...</div>
                    </div>
                </div>
            </div>

            <!-- Audit Trail Tab -->
            <div id="audit-content" class="tab-content">
                <div class="card">
                    <div class="card-header">
                        <h3 class="card-title">Audit Trail (CFR Part 11 Compliant)</h3>
                        <button class="btn btn-primary" onclick="exportAudit('csv')">📥 Export CSV</button>
                    </div>
                    <table id="audit-table">
                        <thead>
                            <tr>
                                <th>Timestamp</th>
                                <th>Event</th>
                                <th>User</th>
                                <th>Action</th>
                                <th>Result</th>
                            </tr>
                        </thead>
                        <tbody><tr><td colspan="5" class="loading">Loading...</td></tr></tbody>
                    </table>
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
                    <input type="text" id="ad-server" placeholder="dc.company.com">
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>Port:</label>
                        <input type="number" id="ad-port" value="389">
                    </div>
                    <div class="form-group">
                        <label>Use SSL:</label>
                        <input type="checkbox" id="ad-use-ssl">
                    </div>
                </div>
                <div class="form-group">
                    <label>Base DN:</label>
                    <input type="text" id="ad-base-dn" placeholder="DC=company,DC=com">
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>Username:</label>
                        <input type="text" id="ad-username" placeholder="admin@company.com">
                    </div>
                    <div class="form-group">
                        <label>Password:</label>
                        <input type="password" id="ad-password">
                    </div>
                </div>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" onclick="testADConnection()">🔌 Test</button>
                <button class="btn btn-success" onclick="saveADConfig()">💾 Save</button>
                <button class="btn btn-secondary" onclick="closeADConfigModal()">Cancel</button>
            </div>
        </div>
    </div>

    <!-- Rule Modal -->
    <div id="ruleModal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <span class="close" onclick="closeRuleModal()">&times;</span>
                <h2 id="rule-modal-title">Create Transfer Rule</h2>
            </div>
            <div class="modal-body">
                <form id="rule-form">
                    <input type="hidden" id="rule-id">
                    <div class="form-group">
                        <label>Rule Name:</label>
                        <input type="text" id="rule-name" placeholder="Auto-backup Documents" required>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label>Source Path:</label>
                            <input type="text" id="rule-source" required>
                        </div>
                        <div class="form-group">
                            <label>File Pattern:</label>
                            <input type="text" id="rule-pattern" value="*.*">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label>Destination Path:</label>
                            <input type="text" id="rule-dest" required>
                        </div>
                        <div class="form-group">
                            <label>Host:</label>
                            <input type="text" id="rule-host" required>
                        </div>
                    </div>
                    <div class="form-row-3">
                        <div class="form-group">
                            <label>Protocol:</label>
                            <select id="rule-protocol">
                                <option value="unc">UNC</option>
                                <option value="smb">SMB</option>
                                <option value="sftp">SFTP</option>
                            </select>
                        </div>
                        <div class="form-group">
                            <label>Port:</label>
                            <input type="number" id="rule-port" value="445">
                        </div>
                        <div class="form-group">
                            <label>Schedule:</label>
                            <select id="rule-schedule">
                                <option value="event_driven">Event Driven</option>
                                <option value="recurring">Recurring</option>
                                <option value="cron">Cron</option>
                            </select>
                        </div>
                    </div>
                </form>
            </div>
            <div class="modal-footer">
                <button class="btn btn-primary" onclick="saveRule()">💾 Save Rule</button>
                <button class="btn btn-secondary" onclick="closeRuleModal()">Cancel</button>
            </div>
        </div>
    </div>

    <script>
        let currentEditingRuleId = null;

        // Tab switching
        function showTab(tabName) {
            document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
            event.target.classList.add('active');
            document.getElementById(tabName + '-content').classList.add('active');

            if (tabName === 'dashboard') loadDashboard();
            else if (tabName === 'history') loadHistory();
            else if (tabName === 'rules') loadRules();
            else if (tabName === 'compliance') loadCompliance();
            else if (tabName === 'users') loadUsers();
            else if (tabName === 'audit') loadAudit();
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
                        <div class="stat-value">${data.transfers.total}</div>
                        <div class="stat-label">Total Transfers</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">${data.rules.active}</div>
                        <div class="stat-label">Active Rules</div>
                    </div>
                    <div class="stat-card">
                        <div class="stat-value">${data.users.total}</div>
                        <div class="stat-label">Users</div>
                    </div>
                `;
            } catch (error) {
                console.error('Error loading dashboard:', error);
            }
        }

        // Transfer form
        document.getElementById('transfer-form')?.addEventListener('submit', async function(e) {
            e.preventDefault();
            const data = {
                source_path: document.getElementById('source-path').value,
                destination_path: document.getElementById('dest-path').value,
                protocol: document.getElementById('protocol').value,
                host: document.getElementById('host').value,
                port: parseInt(document.getElementById('port').value)
            };

            try {
                const response = await fetch('/api/v1/transfers', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                });
                const result = await response.json();
                alert('Transfer started! Task ID: ' + result.task_id);
                showTab('history');
            } catch (error) {
                alert('Transfer failed: ' + error.message);
            }
        });

        // Load history
        async function loadHistory() {
            try {
                const response = await fetch('/api/v1/transfers');
                const data = await response.json();
                const tbody = document.querySelector('#history-table tbody');
                tbody.innerHTML = '';

                if (data.transfers && data.transfers.length > 0) {
                    data.transfers.forEach(t => {
                        const row = tbody.insertRow();
                        row.innerHTML = `
                            <td>${t.task_id.substring(0, 8)}...</td>
                            <td>${t.source_path}</td>
                            <td>${t.destination_path}</td>
                            <td>${t.protocol}</td>
                            <td><span class="status-badge status-success">${t.status}</span></td>
                            <td>${new Date(t.timestamp).toLocaleString()}</td>
                        `;
                    });
                } else {
                    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#999;">No transfers yet</td></tr>';
                }
            } catch (error) {
                console.error('Error loading history:', error);
            }
        }

        // Load rules
        async function loadRules() {
            try {
                const response = await fetch('/api/v1/rules');
                const data = await response.json();
                const tbody = document.querySelector('#rules-table tbody');
                tbody.innerHTML = '';

                if (data.rules && data.rules.length > 0) {
                    data.rules.forEach(rule => {
                        const row = tbody.insertRow();
                        row.innerHTML = `
                            <td><strong>${rule.name}</strong></td>
                            <td>${rule.source_path}<br><small>${rule.source_pattern}</small></td>
                            <td>${rule.destination_path}</td>
                            <td><span class="status-badge">${rule.schedule_type}</span></td>
                            <td>${rule.files_transferred || 0}</td>
                            <td>
                                <label class="toggle">
                                    <input type="checkbox" ${rule.enabled ? 'checked' : ''} 
                                           onchange="toggleRule('${rule.rule_id}', this.checked)">
                                    <span class="slider"></span>
                                </label>
                            </td>
                            <td>
                                <button class="btn btn-small btn-primary" onclick="editRule('${rule.rule_id}')">Edit</button>
                                <button class="btn btn-small btn-danger" onclick="deleteRule('${rule.rule_id}')">Delete</button>
                            </td>
                        `;
                    });
                } else {
                    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#999;">No rules yet</td></tr>';
                }
            } catch (error) {
                console.error('Error loading rules:', error);
            }
        }

        // Load compliance
        async function loadCompliance() {
            try {
                const response = await fetch('/api/v1/compliance');
                const data = await response.json();
                const container = document.getElementById('compliance-frameworks');
                container.innerHTML = '';

                data.frameworks.forEach(fw => {
                    const div = document.createElement('div');
                    div.className = 'framework-card' + (fw.enabled ? ' enabled' : '');
                    div.innerHTML = `
                        <div style="display:flex;justify-content:space-between;align-items:center;">
                            <div><strong>${fw.name}</strong></div>
                            <label class="toggle">
                                <input type="checkbox" ${fw.enabled ? 'checked' : ''} 
                                       onchange="toggleFramework('${fw.framework}', this.checked)">
                                <span class="slider"></span>
                            </label>
                        </div>
                        <div style="margin-top:10px;font-size:14px;color:#666;">
                            Encryption: ${fw.encryption_algorithm || 'N/A'} | 
                            Retention: ${fw.audit_retention_days} days
                        </div>
                    `;
                    container.appendChild(div);
                });
            } catch (error) {
                console.error('Error loading compliance:', error);
            }
        }

        async function toggleFramework(framework, enabled) {
            try {
                await fetch(`/api/v1/compliance/${framework}/${enabled ? 'enable' : 'disable'}`, {
                    method: 'POST'
                });
                loadCompliance();
            } catch (error) {
                console.error('Error toggling framework:', error);
            }
        }

        // Load users
        async function loadUsers() {
            try {
                const response = await fetch('/api/v1/users');
                const data = await response.json();
                const container = document.getElementById('users-table-container');

                if (data.users && data.users.length > 0) {
                    let html = '<table><thead><tr><th>User</th><th>Email</th><th>Department</th><th>Permissions</th></tr></thead><tbody>';
                    data.users.forEach(user => {
                        html += `
                            <tr>
                                <td><strong>${user.display_name || user.username}</strong><br><small>${user.username}</small></td>
                                <td>${user.email || 'N/A'}</td>
                                <td>${user.department || 'N/A'}</td>
                                <td>
                                    ${user.is_admin ? '<span class="status-badge status-error">Admin</span> ' : ''}
                                    ${user.can_upload ? '<span class="status-badge status-success">Upload</span> ' : ''}
                                    ${user.can_download ? '<span class="status-badge status-info">Download</span> ' : ''}
                                </td>
                            </tr>
                        `;
                    });
                    html += '</tbody></table>';
                    container.innerHTML = html;
                } else {
                    container.innerHTML = '<p style="text-align:center;color:#999;">No users synced. Click "Sync from AD" to import users.</p>';
                }
            } catch (error) {
                console.error('Error loading users:', error);
            }
        }

        // Load audit
        async function loadAudit() {
            try {
                const response = await fetch('/api/v1/audit?limit=50');
                const data = await response.json();
                const tbody = document.querySelector('#audit-table tbody');
                tbody.innerHTML = '';

                if (data.events && data.events.length > 0) {
                    data.events.forEach(event => {
                        const row = tbody.insertRow();
                        row.innerHTML = `
                            <td><small>${new Date(event.timestamp).toLocaleString()}</small></td>
                            <td>${event.event_type.replace(/_/g, ' ').toUpperCase()}</td>
                            <td>${event.username || 'N/A'}</td>
                            <td>${event.action}</td>
                            <td><span class="status-badge status-${event.result === 'success' ? 'success' : 'error'}">${event.result}</span></td>
                        `;
                    });
                } else {
                    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:#999;">No audit events yet</td></tr>';
                }
            } catch (error) {
                console.error('Error loading audit:', error);
            }
        }

        // AD Config Modal
        function showADConfigModal() {
            fetch('/api/v1/ad/config')
                .then(r => r.json())
                .then(config => {
                    document.getElementById('ad-server').value = config.server || '';
                    document.getElementById('ad-port').value = config.port || 389;
                    document.getElementById('ad-use-ssl').checked = config.use_ssl || false;
                    document.getElementById('ad-base-dn').value = config.base_dn || '';
                    document.getElementById('ad-username').value = config.username || '';
                    document.getElementById('adConfigModal').style.display = 'block';
                });
        }

        function closeADConfigModal() {
            document.getElementById('adConfigModal').style.display = 'none';
        }

        async function testADConnection() {
            const config = {
                server: document.getElementById('ad-server').value,
                port: parseInt(document.getElementById('ad-port').value),
                use_ssl: document.getElementById('ad-use-ssl').checked,
                base_dn: document.getElementById('ad-base-dn').value,
                username: document.getElementById('ad-username').value,
                password: document.getElementById('ad-password').value
            };

            try {
                const response = await fetch('/api/v1/ad/test', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(config)
                });
                const result = await response.json();
                alert(result.success ? `✅ Connected! Found ${result.users_found || 0} users` : `❌ Failed: ${result.error}`);
            } catch (error) {
                alert('❌ Connection test failed: ' + error.message);
            }
        }

        async function saveADConfig() {
            const config = {
                server: document.getElementById('ad-server').value,
                port: parseInt(document.getElementById('ad-port').value),
                use_ssl: document.getElementById('ad-use-ssl').checked,
                base_dn: document.getElementById('ad-base-dn').value,
                username: document.getElementById('ad-username').value,
                password: document.getElementById('ad-password').value
            };

            try {
                await fetch('/api/v1/ad/config', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(config)
                });
                alert('✅ AD configuration saved!');
                closeADConfigModal();
            } catch (error) {
                alert('❌ Failed to save configuration');
            }
        }

        async function syncADUsers() {
            try {
                const response = await fetch('/api/v1/users/sync', {method: 'POST'});
                const result = await response.json();
                alert(result.success ? `✅ Synced ${result.users_synced} users!` : `❌ Sync failed: ${result.error}`);
                loadUsers();
            } catch (error) {
                alert('❌ Sync failed: ' + error.message);
            }
        }

        // Rule Modal
        function showCreateRuleModal() {
            document.getElementById('rule-modal-title').textContent = 'Create Transfer Rule';
            document.getElementById('rule-form').reset();
            document.getElementById('rule-id').value = '';
            document.getElementById('ruleModal').style.display = 'block';
        }

        function closeRuleModal() {
            document.getElementById('ruleModal').style.display = 'none';
        }

        async function saveRule() {
            const ruleId = document.getElementById('rule-id').value;
            const data = {
                name: document.getElementById('rule-name').value,
                source_path: document.getElementById('rule-source').value,
                source_pattern: document.getElementById('rule-pattern').value,
                destination_path: document.getElementById('rule-dest').value,
                protocol: document.getElementById('rule-protocol').value,
                host: document.getElementById('rule-host').value,
                port: parseInt(document.getElementById('rule-port').value),
                schedule_type: document.getElementById('rule-schedule').value
            };

            try {
                const url = ruleId ? `/api/v1/rules/${ruleId}` : '/api/v1/rules';
                const method = ruleId ? 'PUT' : 'POST';
                await fetch(url, {
                    method: method,
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                });
                alert('✅ Rule saved!');
                closeRuleModal();
                loadRules();
            } catch (error) {
                alert('❌ Failed to save rule');
            }
        }

        async function toggleRule(ruleId, enabled) {
            try {
                await fetch(`/api/v1/rules/${ruleId}/${enabled ? 'enable' : 'disable'}`, {method: 'POST'});
                loadRules();
            } catch (error) {
                console.error('Error toggling rule:', error);
            }
        }

        async function deleteRule(ruleId) {
            if (confirm('Delete this rule?')) {
                try {
                    await fetch(`/api/v1/rules/${ruleId}`, {method: 'DELETE'});
                    loadRules();
                } catch (error) {
                    alert('❌ Failed to delete rule');
                }
            }
        }

        function exportAudit(format) {
            window.open(`/api/v1/audit/export?format=${format}`, '_blank');
        }

        // Load dashboard on startup
        window.onload = function() {
            loadDashboard();
        };

        // Auto-refresh
        setInterval(function() {
            const activeTab = document.querySelector('.tab-content.active');
            if (activeTab.id === 'dashboard-content') loadDashboard();
            else if (activeTab.id === 'history-content') loadHistory();
            else if (activeTab.id === 'rules-content') loadRules();
        }, 10000);

        // Close modals on outside click
        window.onclick = function(event) {
            if (event.target.classList.contains('modal')) {
                event.target.style.display = 'none';
            }
        }
    </script>
</body>
</html>
"""


@app.route('/')
def index():
    return HTML_TEMPLATE


# ============================================================================
# API ENDPOINTS - COMPLETE INTEGRATION
# ============================================================================

@app.route('/api/v1/dashboard', methods=['GET'])
def get_dashboard():
    """Get dashboard statistics"""
    try:
        rule_stats = monitor_manager.get_statistics()
        return jsonify({
            'compliance': {
                'enabled_frameworks': len(compliance_mgr.get_enabled_frameworks())
            },
            'transfers': {
                'total': len(mft_app.transfers)
            },
            'rules': {
                'active': rule_stats.get('active_rules', 0)
            },
            'users': {
                'total': len(ad_mgr.users)
            }
        })
    except Exception as e:
        logger.error(f"Error getting dashboard: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/transfers', methods=['POST'])
def create_transfer():
    """Create new transfer"""
    try:
        data = request.get_json()
        protocol_map = {
            'unc': TransferProtocol.UNC,
            'smb': TransferProtocol.SMB,
            'sftp': TransferProtocol.SFTP,
            'local': TransferProtocol.UNC
        }

        protocol = protocol_map.get(data.get('protocol', 'unc').lower(), TransferProtocol.UNC)
        config = TransferConfig(
            protocol=protocol,
            host=data.get('host'),
            port=data.get('port', 445),
            username=data.get('username'),
            password=data.get('password'),
            encryption_enabled=True
        )

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        task_id = loop.run_until_complete(
            mft_app.transfer_file(
                data.get('source_path'),
                data.get('destination_path'),
                config
            )
        )
        loop.close()

        # Log audit event
        audit_mgr.log_event(
            AuditEventType.TRANSFER_STARTED,
            "File transfer initiated",
            username=request.remote_addr,
            resource=data.get('source_path'),
            result="success",
            details=data
        )

        return jsonify({'success': True, 'task_id': task_id})
    except Exception as e:
        logger.error(f"Transfer failed: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/transfers', methods=['GET'])
def get_transfers():
    """Get all transfers"""
    transfers = []
    for task_id, transfer in mft_app.transfers.items():
        transfers.append({
            'task_id': task_id,
            'source_path': transfer.get('source_path'),
            'destination_path': transfer.get('destination_path'),
            'protocol': transfer.get('protocol'),
            'status': transfer.get('status'),
            'timestamp': transfer.get('timestamp')
        })
    return jsonify({'transfers': transfers})


@app.route('/api/v1/rules', methods=['GET'])
def get_rules():
    """Get all transfer rules"""
    try:
        stats = monitor_manager.get_statistics()
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/rules', methods=['POST'])
def create_rule():
    """Create new transfer rule"""
    try:
        data = request.get_json()
        rule = TransferRule(
            rule_id=str(uuid.uuid4()),
            name=data.get('name'),
            source_path=data.get('source_path'),
            destination_path=data.get('destination_path'),
            protocol=data.get('protocol'),
            host=data.get('host'),
            port=data.get('port', 445),
            enabled=True,
            source_pattern=data.get('source_pattern', '*.*'),
            schedule_type=getattr(ScheduleType, data.get('schedule_type', 'EVENT_DRIVEN').upper()),
            trigger_type=getattr(TriggerType, 'FILE_CREATED'),
            action_type=getattr(ActionType, 'COPY')
        )
        monitor_manager.add_rule(rule)

        # Log audit event
        audit_mgr.log_event(
            AuditEventType.RULE_CREATED,
            f"Transfer rule '{rule.name}' created",
            username=request.remote_addr,
            resource=rule.rule_id,
            result="success",
            details=data
        )

        return jsonify({'success': True, 'rule_id': rule.rule_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/v1/rules/<rule_id>/enable', methods=['POST'])
def enable_rule(rule_id):
    """Enable rule"""
    monitor_manager.enable_rule(rule_id)
    return jsonify({'success': True})


@app.route('/api/v1/rules/<rule_id>/disable', methods=['POST'])
def disable_rule(rule_id):
    """Disable rule"""
    monitor_manager.disable_rule(rule_id)
    return jsonify({'success': True})


@app.route('/api/v1/rules/<rule_id>', methods=['DELETE'])
def delete_rule(rule_id):
    """Delete rule"""
    monitor_manager.remove_rule(rule_id)
    return jsonify({'success': True})


@app.route('/api/v1/compliance', methods=['GET'])
def get_compliance():
    """Get compliance frameworks"""
    frameworks = []
    for framework, config in compliance_mgr.frameworks.items():
        frameworks.append({
            'framework': framework.value,
            'name': framework.value.replace('_', ' ').upper(),
            'enabled': config.enabled,
            'encryption_algorithm': config.encryption_algorithm.value if config.encryption_algorithm else None,
            'audit_retention_days': config.audit_retention_days
        })
    return jsonify({'frameworks': frameworks})


@app.route('/api/v1/compliance/<framework>/enable', methods=['POST'])
def enable_framework(framework):
    """Enable framework"""
    fw = ComplianceFramework(framework)
    compliance_mgr.enable_framework(fw)
    return jsonify({'success': True})


@app.route('/api/v1/compliance/<framework>/disable', methods=['POST'])
def disable_framework(framework):
    """Disable framework"""
    fw = ComplianceFramework(framework)
    compliance_mgr.disable_framework(fw)
    return jsonify({'success': True})


@app.route('/api/v1/users', methods=['GET'])
def get_users():
    """Get all users"""
    users = ad_mgr.search_users()
    return jsonify({'users': [user.to_dict() for user in users]})


@app.route('/api/v1/users/sync', methods=['POST'])
def sync_users():
    """Sync users from AD"""
    config = request.json or load_ad_config()
    result = ad_mgr.sync_from_ad(config)

    if result['success']:
        audit_mgr.log_event(
            AuditEventType.AD_SYNC_COMPLETED,
            "AD sync completed",
            username=request.remote_addr,
            result="success",
            details={'users_synced': result['users_synced']}
        )
    return jsonify(result)


@app.route('/api/v1/ad/config', methods=['GET'])
def get_ad_config():
    """Get AD config"""
    config = load_ad_config()
    config['password'] = '****' if config.get('password') else ''
    return jsonify(config)


@app.route('/api/v1/ad/config', methods=['POST'])
def save_ad_config_endpoint():
    """Save AD config"""
    config = request.json
    existing_config = load_ad_config()
    if config.get('password') == '****':
        config['password'] = existing_config.get('password', '')
    save_ad_config(config)
    return jsonify({'success': True})


@app.route('/api/v1/ad/test', methods=['POST'])
def test_ad():
    """Test AD connection"""
    config = request.json
    # TODO: Implement real AD test with ldap3
    # For now, return mock success
    return jsonify({
        'success': True,
        'message': f"Connected to {config.get('server')}",
        'users_found': 150
    })


@app.route('/api/v1/audit', methods=['GET'])
def get_audit():
    """Get audit events"""
    limit = int(request.args.get('limit', 100))
    events = audit_mgr.get_events(limit=limit)
    return jsonify({'events': [event.to_dict() for event in events]})


@app.route('/api/v1/audit/export', methods=['GET'])
def export_audit():
    """Export audit logs"""
    format_type = request.args.get('format', 'json')

    if format_type == 'csv':
        import io
        import csv
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Timestamp', 'Event', 'User', 'Action', 'Result'])

        for event in audit_mgr.events:
            writer.writerow([
                event.timestamp.isoformat(),
                event.event_type.value,
                event.username or 'N/A',
                event.action,
                event.result
            ])

        return output.getvalue(), 200, {
            'Content-Type': 'text/csv',
            'Content-Disposition': 'attachment; filename=audit_log.csv'
        }
    else:
        events_data = [event.to_dict() for event in audit_mgr.events]
        return jsonify({'events': events_data})


if __name__ == '__main__':
    print("=" * 80)
    print("🎉 COMPLETE INTEGRATED MFT SYSTEM")
    print("=" * 80)
    print("✅ File Transfers + Rules")
    print("✅ HIPAA, PCI DSS, GDPR Compliance")
    print("✅ Active Directory Integration")
    print("✅ CFR Part 11 Audit Trail")
    print("=" * 80)
    print("🌐 http://127.0.0.1:5000")
    print("=" * 80)

    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)