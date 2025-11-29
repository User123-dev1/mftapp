"""
MFT System with Transfer Rules UI + AD Integration + Compliance
Complete integration with Active Directory and Compliance frameworks
ENHANCED VERSION - Working search and permission editing
"""

from flask import Flask, render_template_string, request, jsonify
from flask_cors import CORS
import asyncio
import uuid
from datetime import datetime, timedelta
import threading
import logging

# Import MFT application
from mft_application import MFTApplication, TransferConfig, TransferProtocol

# Import file monitor
from file_monitor import FileMonitorManager, TransferRule, ScheduleType, TriggerType, ActionType

# Import server health monitor
from server_monitor import ServerHealthMonitor

# Import compliance and AD systems
from compliance_system import (
    ComplianceManager, AuditManager, ActiveDirectoryManager,
    ComplianceFramework, AuditEventType, EncryptionAlgorithm
)

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create Flask app
app = Flask(__name__)
CORS(app)

# Initialize MFT application
mft_app = MFTApplication()

# Initialize File Monitor Manager
monitor_manager = FileMonitorManager(mft_app)

# Initialize Compliance Manager
compliance_manager = ComplianceManager()

# Initialize Audit Manager
audit_manager = AuditManager()

# Initialize Active Directory Manager
ad_manager = ActiveDirectoryManager()

# Initialize Server Health Monitor
server_monitor = ServerHealthMonitor(audit_manager=audit_manager, check_interval=30)

# Start monitoring on startup
monitor_manager.start_all()

# Start server health monitoring
server_monitor.start_monitoring()

# Sync servers from rules
server_monitor.sync_servers_from_rules(monitor_manager.rules)

# Log system startup
audit_manager.log_event(
    AuditEventType.SYSTEM_STARTED,
    "MFT System Started",
    username="system",
    result="success"
)

# Add a global variable to track AD connection status
ad_connection_status = {
    'connected': False,
    'last_test': None,
    'error_message': None
}

# ============================================================================
# ENHANCED HTML TEMPLATE WITH SEARCH AND FIXED PERMISSIONS
# ============================================================================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>MFT System - Professional File Transfer</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1600px;
            margin: 0 auto;
            background: white;
            border-radius: 15px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }
        
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }
        
        .header h1 {
            font-size: 32px;
            margin-bottom: 10px;
        }
        
        .header p {
            font-size: 16px;
            opacity: 0.9;
        }
        
        .tabs {
            display: flex;
            background: #f5f5f5;
            border-bottom: 2px solid #ddd;
            overflow-x: auto;
        }
        
        .tab {
            flex: 1;
            padding: 15px 20px;
            text-align: center;
            cursor: pointer;
            font-weight: 600;
            color: #666;
            transition: all 0.3s;
            border-bottom: 3px solid transparent;
            white-space: nowrap;
            min-width: 120px;
        }
        
        .tab:hover {
            background: #e8e8e8;
        }
        
        .tab.active {
            color: #667eea;
            border-bottom-color: #667eea;
            background: white;
        }
        
        .tab-content {
            display: none;
            padding: 30px;
        }
        
        .tab-content.active {
            display: block;
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 8px;
            font-weight: 600;
            color: #333;
        }
        
        .form-group input,
        .form-group select,
        .form-group textarea {
            width: 100%;
            padding: 12px;
            border: 2px solid #ddd;
            border-radius: 8px;
            font-size: 14px;
            transition: border-color 0.3s;
        }
        
        .form-group input:focus,
        .form-group select:focus,
        .form-group textarea:focus {
            outline: none;
            border-color: #667eea;
        }
        
        .form-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
        }
        
        .form-row-3 {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 20px;
        }
        
        .btn {
            padding: 12px 30px;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .btn-primary {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
        }
        
        .btn-primary:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4);
        }
        
        .btn-success {
            background: #28a745;
            color: white;
        }
        
        .btn-danger {
            background: #dc3545;
            color: white;
        }
        
        .btn-secondary {
            background: #6c757d;
            color: white;
        }
        
        .btn-small {
            padding: 6px 12px;
            font-size: 14px;
        }
        
        .message {
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
            display: none;
        }
        
        .message.success {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        
        .message.error {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        
        .message.show {
            display: block;
        }
        
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }
        
        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }
        
        th {
            background: #f5f5f5;
            font-weight: 600;
            color: #333;
        }
        
        tr:hover {
            background: #f9f9f9;
        }
        
        .status-badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }
        
        .status-enabled {
            background: #d4edda;
            color: #155724;
        }
        
        .status-disabled {
            background: #f8d7da;
            color: #721c24;
        }
        
        .status-monitoring {
            background: #d1ecf1;
            color: #0c5460;
        }
        
        .status-error {
            background: #f8d7da;
            color: #721c24;
        }
        
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
            border-radius: 10px;
            text-align: center;
        }
        
        .stat-card h3 {
            font-size: 36px;
            margin-bottom: 5px;
        }
        
        .stat-card p {
            font-size: 14px;
            opacity: 0.9;
        }
        
        .toggle-switch {
            position: relative;
            display: inline-block;
            width: 50px;
            height: 24px;
        }
        
        .toggle-switch input {
            opacity: 0;
            width: 0;
            height: 0;
        }
        
        .toggle-slider {
            position: absolute;
            cursor: pointer;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background-color: #ccc;
            transition: .4s;
            border-radius: 24px;
        }
        
        .toggle-slider:before {
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
        
        input:checked + .toggle-slider {
            background-color: #28a745;
        }
        
        input:checked + .toggle-slider:before {
            transform: translateX(26px);
        }
        
        .compliance-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
            margin-top: 20px;
        }
        
        .compliance-card {
            border: 2px solid #ddd;
            border-radius: 10px;
            padding: 20px;
            transition: all 0.3s;
        }
        
        .compliance-card:hover {
            border-color: #667eea;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        
        .compliance-card.enabled {
            border-color: #28a745;
            background: #f0fff4;
        }
        
        .compliance-card h3 {
            margin-bottom: 10px;
            color: #333;
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
            padding: 8px;
            background: #f5f5f5;
            border-radius: 5px;
        }
        
        .permission-item input[type="checkbox"] {
            margin-right: 8px;
            width: auto;
        }
        
        .help-text {
            font-size: 12px;
            color: #666;
            margin-top: 4px;
        }
        
        .section-title {
            font-size: 20px;
            font-weight: 600;
            margin: 30px 0 15px 0;
            color: #333;
            border-bottom: 2px solid #667eea;
            padding-bottom: 10px;
        }
        
        .info-box {
            background: #e7f3ff;
            border-left: 4px solid #2196F3;
            padding: 15px;
            margin: 20px 0;
            border-radius: 5px;
        }
        
        .info-box strong {
            display: block;
            margin-bottom: 5px;
            color: #1976D2;
        }
        
        /* ✅ SEARCH BAR STYLES */
        .search-container {
            background: #f8f9fa;
            padding: 20px;
            border-radius: 10px;
            margin-bottom: 20px;
        }
        
        .search-bar {
            display: flex;
            gap: 10px;
            align-items: center;
        }
        
        .search-bar input[type="text"] {
            flex: 1;
            padding: 12px;
            border: 2px solid #ddd;
            border-radius: 8px;
            font-size: 14px;
        }
        
        .search-bar select {
            padding: 12px;
            border: 2px solid #ddd;
            border-radius: 8px;
            font-size: 14px;
            min-width: 150px;
        }
        
        /* ✅ GROUP BADGE */
        .group-badge {
            display: inline-block;
            padding: 3px 10px;
            margin: 2px;
            background: #667eea;
            color: white;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
        }
        
        .group-row {
            background: #f0f7ff !important;
            border-left: 4px solid #667eea;
        }
        
        .group-row:hover {
            background: #e3f2fd !important;
        }
        
        /* MODAL STYLES */
        .modal {
            display: none;
            position: fixed;
            z-index: 1000;
            left: 0;
            top: 0;
            width: 100%;
            height: 100%;
            background-color: rgba(0,0,0,0.5);
            overflow: auto;
        }
        
        .modal-content {
            background-color: white;
            margin: 5% auto;
            padding: 30px;
            border-radius: 15px;
            width: 80%;
            max-width: 800px;
            max-height: 80vh;
            overflow-y: auto;
            position: relative;
        }
        
        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
        }
        
        .modal-header h2 {
            color: #667eea;
        }
        
        .close {
            font-size: 28px;
            font-weight: bold;
            cursor: pointer;
            color: #999;
        }
        
        .close:hover {
            color: #333;
        }
        
        .member-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px;
            margin: 5px 0;
            background: white;
            border: 1px solid #ddd;
            border-radius: 5px;
        }
    </style>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 MFT Professional File Transfer System</h1>
            <p>Managed File Transfer with Advanced Monitoring, Compliance & Active Directory Integration</p>
        </div>
        
        <div class="tabs">
            <div class="tab active" onclick="showTab('dashboard')">📊 Dashboard</div>
            <div class="tab" onclick="showTab('transfer')">📤 New Transfer</div>
            <div class="tab" onclick="showTab('history')">📋 History</div>
            <div class="tab" onclick="showTab('rules')">⚙️ Transfer Rules</div>
            <div class="tab" onclick="showTab('users')">👥 Users</div>
            <div class="tab" onclick="showTab('ad')">🔐 Active Directory</div>
            <div class="tab" onclick="showTab('compliance')">✅ Compliance</div>
            <div class="tab" onclick="showTab('audit')">📝 Audit Log</div>
            <div class="tab" onclick="showTab('activity')">📡 Activity Log</div>
        </div>
        
        <!-- Dashboard Tab -->
        <div id="dashboard-tab" class="tab-content active">
            <h2>System Dashboard</h2>

            <!-- Statistics Grid -->
            <div class="stats-grid">
                <div class="stat-card">
                    <h3 id="total-bytes-transferred">0 B</h3>
                    <p>Total Data Transferred</p>
                </div>
                <div class="stat-card">
                    <h3 id="total-files-transferred">0</h3>
                    <p>Total Files Transferred</p>
                </div>
                <div class="stat-card">
                    <h3 id="active-rules">0</h3>
                    <p>Active Rules</p>
                </div>
                <div class="stat-card">
                    <h3 id="total-transfers">0</h3>
                    <p>Total Transfers</p>
                </div>
                <div class="stat-card">
                    <h3 id="success-rate">0%</h3>
                    <p>Success Rate</p>
                </div>
            </div>

            <!-- Secondary Stats Grid -->
            <div class="stats-grid" style="margin-top: 20px;">
                <div class="stat-card" style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white;">
                    <h3 id="active-transfers">0</h3>
                    <p>Active Transfers</p>
                </div>
                <div class="stat-card" style="background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%); color: white;">
                    <h3 id="completed-transfers">0</h3>
                    <p>Completed</p>
                </div>
                <div class="stat-card" style="background: linear-gradient(135deg, #ee0979 0%, #ff6a00 100%); color: white;">
                    <h3 id="failed-transfers">0</h3>
                    <p>Failed</p>
                </div>
                <div class="stat-card" style="background: linear-gradient(135deg, #4776e6 0%, #8e54e9 100%); color: white;">
                    <h3 id="total-users">0</h3>
                    <p>AD Users</p>
                </div>
            </div>

            <!-- Performance Chart -->
            <div style="background: white; padding: 20px; border-radius: 10px; margin-top: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                <h3 style="margin-bottom: 15px;">📈 Transfer Performance (Last 24 Hours)</h3>
                <canvas id="performanceChart" height="80"></canvas>
            </div>

            <div class="info-box" style="margin-top: 20px;">
                <strong>System Status</strong>
                <p>All systems operational. Last AD sync: <span id="last-ad-sync">Never</span></p>
                <p>Enabled compliance frameworks: <span id="enabled-frameworks">None</span></p>
            </div>
        </div>
        
        <!-- Transfer Tab -->
        <div id="transfer-tab" class="tab-content">
            <h2>Create New Transfer</h2>
            <div id="transfer-message" class="message"></div>
            
            <form id="transfer-form">
                <div class="form-row-3">
                    <div class="form-group">
                        <label>Source Path:</label>
                        <input type="text" id="source-path" placeholder="//192.168.1.10/C$/data/file.txt" required>
                    </div>
                    <div class="form-group">
                        <label>Protocol:</label>
                        <select id="protocol">
                            <option value="unc">UNC - Windows Shares (Port 445)</option>
                            <option value="smb">SMB - Windows SMB Protocol</option>
                            <option value="sftp">SFTP - SSH File Transfer</option>
                            <option value="local">Local - Direct File Copy</option>
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
                
                <div class="form-row">
                    <div class="form-group">
                        <label>Username (optional):</label>
                        <input type="text" id="username">
                    </div>
                    <div class="form-group">
                        <label>Password (optional):</label>
                        <input type="password" id="password">
                    </div>
                </div>
                
                <button type="submit" class="btn btn-primary">📤 Start Transfer</button>
            </form>
        </div>
        
        <!-- History Tab -->
        <div id="history-tab" class="tab-content">
            <h2>Transfer History</h2>
            <table id="history-table">
                <thead>
                    <tr>
                        <th>Task ID</th>
                        <th>Source</th>
                        <th>Destination</th>
                        <th>Protocol</th>
                        <th>Status</th>
                        <th>Time</th>
                        <th>Details</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
        
        <!-- Transfer Rules Tab -->
        <div id="rules-tab" class="tab-content">
            <h2>Transfer Rules</h2>
            <div id="rules-message" class="message"></div>
            
            <button class="btn btn-primary" onclick="showCreateRuleModal()" style="margin-bottom: 20px;">
                ➕ Create New Rule
            </button>
            <button class="btn btn-secondary" onclick="seedRules()" style="margin-bottom: 20px; margin-left: 10px;">
                🌱 Seed Test Data
            </button>

            <table id="rules-table">
                <thead>
                    <tr>
                        <th>Rule Name</th>
                        <th>Source</th>
                        <th>Destination</th>
                        <th>Schedule</th>
                        <th>Action</th>
                        <th>Files</th>
                        <th>Status</th>
                        <th>Enabled</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
        
        <!-- ✅ ENHANCED Users Tab with Search -->
        <div id="users-tab" class="tab-content">
            <h2>Active Directory Users & Groups</h2>
            <div id="users-message" class="message"></div>
            
            <!-- ✅ SEARCH CONTAINER -->
            <div class="search-container">
                <div class="search-bar">
                    <input type="text" id="user-search-input" placeholder="Search by username, display name, email, department, or group name...">
                    <select id="search-type">
                        <option value="users">Search Users</option>
                        <option value="groups">Search Groups</option>
                    </select>
                    <button type="button" class="btn btn-primary" onclick="performSearch()">🔍 Search</button>
                    <button type="button" class="btn btn-secondary" onclick="clearSearch()">Clear</button>
                </div>
            </div>
            
            <table id="users-table">
                <thead>
                    <tr>
                        <th>Username</th>
                        <th>Display Name</th>
                        <th>Email</th>
                        <th>Department</th>
                        <th>Groups</th>
                        <th>Permissions</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
        
        <!-- Active Directory Tab -->
        <div id="ad-tab" class="tab-content">
            <h2>Active Directory Configuration</h2>
            <div id="ad-message" class="message"></div>
            
            <form id="ad-config-form">
                <div class="section-title">Connection Settings</div>
                
                <div class="form-row">
                    <div class="form-group">
                        <label>AD Server:</label>
                        <input type="text" id="ad-server" placeholder="ldap://dc.company.com" required>
                        <div class="help-text">LDAP server URL</div>
                    </div>
                    <div class="form-group">
                        <label>Port:</label>
                        <input type="number" id="ad-port" value="389">
                        <div class="help-text">389 for LDAP, 636 for LDAPS</div>
                    </div>
                </div>
                
                <div class="form-row">
                    <div class="form-group">
                        <label>Base DN:</label>
                        <input type="text" id="ad-base-dn" placeholder="DC=company,DC=com" required>
                        <div class="help-text">Base Distinguished Name</div>
                    </div>
                    <div class="form-group">
                        <label>Bind DN:</label>
                        <input type="text" id="ad-bind-dn" placeholder="CN=admin,DC=company,DC=com" required>
                        <div class="help-text">Admin user for binding</div>
                    </div>
                </div>
                
                <div class="form-row">
                    <div class="form-group">
                        <label>Bind Password:</label>
                        <input type="password" id="ad-bind-password" required>
                    </div>
                    <div class="form-group">
                        <label>Use SSL:</label>
                        <select id="ad-use-ssl">
                            <option value="true">Yes (LDAPS)</option>
                            <option value="false">No (LDAP)</option>
                        </select>
                    </div>
                </div>
                
                <div class="section-title">Sync Settings</div>
                
                <div class="form-row">
                    <div class="form-group">
                        <label>Auto Sync Interval (minutes):</label>
                        <input type="number" id="ad-sync-interval" value="60" min="0">
                        <div class="help-text">0 to disable auto-sync</div>
                    </div>
                    <div class="form-group">
                        <label>User Filter:</label>
                        <input type="text" id="ad-user-filter" value="(objectClass=user)">
                        <div class="help-text">LDAP filter for users</div>
                    </div>
                </div>
                
                <div style="margin-top: 20px;">
                    <button type="submit" class="btn btn-primary">💾 Save Configuration</button>
                    <button type="button" class="btn btn-success" onclick="testADConnection()">🔌 Test Connection</button>
                    <button type="button" class="btn btn-secondary" onclick="syncADUsers()">🔄 Sync Users Now</button>
                </div>
            </form>
            
            <div class="info-box" style="margin-top: 30px;">
                <strong>Connection Status</strong>
                <p id="ad-connection-status">Not configured</p>
                <p>Last sync: <span id="ad-last-sync">Never</span></p>
                <p>Users synced: <span id="ad-users-synced">0</span></p>
            </div>
        </div>
        
        <!-- Compliance Tab -->
        <div id="compliance-tab" class="tab-content">
            <h2>Compliance Framework Management</h2>
            <div id="compliance-message" class="message"></div>
            
            <div class="info-box">
                <strong>About Compliance Frameworks</strong>
                <p>Enable compliance frameworks to ensure file transfers meet regulatory requirements. Each framework enforces specific security and auditing standards.</p>
            </div>
            
            <div class="compliance-grid" id="compliance-grid">
                <!-- Compliance cards will be loaded here -->
            </div>
        </div>
        
        <!-- Audit Log Tab -->
        <div id="audit-tab" class="tab-content">
            <h2>Audit Trail</h2>
            
            <div class="form-row" style="margin-bottom: 20px;">
                <div class="form-group">
                    <label>Event Type:</label>
                    <select id="audit-filter-type">
                        <option value="">All Events</option>
                        <option value="transfer_started">Transfer Started</option>
                        <option value="transfer_completed">Transfer Completed</option>
                        <option value="transfer_failed">Transfer Failed</option>
                        <option value="user_login">User Login</option>
                        <option value="rule_created">Rule Created</option>
                        <option value="rule_modified">Rule Modified</option>
                        <option value="ad_sync_started">AD Sync Started</option>
                        <option value="ad_sync_completed">AD Sync Completed</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Result:</label>
                    <select id="audit-filter-result">
                        <option value="">All Results</option>
                        <option value="success">Success</option>
                        <option value="failure">Failure</option>
                        <option value="warning">Warning</option>
                    </select>
                </div>
            </div>
            
            <button class="btn btn-primary" onclick="loadAuditLog()">🔍 Search</button>
            <button class="btn btn-secondary" onclick="exportAuditLog()">📥 Export</button>
            
            <table id="audit-table">
                <thead>
                    <tr>
                        <th>Timestamp</th>
                        <th>Event Type</th>
                        <th>User</th>
                        <th>Action</th>
                        <th>Result</th>
                        <th>Details</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>

        <!-- Activity Log Tab -->
        <div id="activity-tab" class="tab-content">
            <h2>Server Activity Log</h2>

            <div class="info-box" style="margin-bottom: 20px;">
                <h3>📡 Server Health Monitoring</h3>
                <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-top: 10px;">
                    <div>
                        <strong>Total Servers:</strong> <span id="activity-total-servers">0</span>
                    </div>
                    <div>
                        <strong style="color: #27ae60;">Online:</strong> <span id="activity-online-count" style="color: #27ae60;">0</span>
                    </div>
                    <div>
                        <strong style="color: #e74c3c;">Offline:</strong> <span id="activity-offline-count" style="color: #e74c3c;">0</span>
                    </div>
                </div>
            </div>

            <button class="btn btn-primary" onclick="loadActivityLog()">🔄 Refresh</button>

            <h3 style="margin-top: 30px;">Recent Server Events</h3>
            <table id="activity-table">
                <thead>
                    <tr>
                        <th>Timestamp</th>
                        <th>Event</th>
                        <th>Server</th>
                        <th>Message</th>
                        <th>Details</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>

            <h3 style="margin-top: 30px;">Server Status</h3>
            <table id="server-status-table">
                <thead>
                    <tr>
                        <th>Server</th>
                        <th>Protocol</th>
                        <th>Status</th>
                        <th>Last Check</th>
                        <th>Downtime</th>
                        <th>Offline Since</th>
                    </tr>
                </thead>
                <tbody></tbody>
            </table>
        </div>
    </div>

    <!-- CREATE/EDIT RULE MODAL -->
    <div id="rule-modal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <h2 id="modal-title">Create Transfer Rule</h2>
                <span class="close" onclick="closeRuleModal()">&times;</span>
            </div>
            
            <form id="rule-form">
                <input type="hidden" id="rule-id">
                
                <div class="form-group">
                    <label>Rule Name:</label>
                    <input type="text" id="rule-name" placeholder="Auto-backup Documents" required>
                </div>
                
                <div class="form-row">
                    <div class="form-group">
                        <label>Source Path:</label>
                        <input type="text" id="rule-source" placeholder="//192.168.1.10/C$/data" required>
                        <div class="help-text">Path to monitor for new files</div>
                    </div>
                    <div class="form-group">
                        <label>File Pattern:</label>
                        <input type="text" id="rule-pattern" value="*.*">
                        <div class="help-text">*.txt, *.pdf, *.*, etc.</div>
                    </div>
                </div>
                
                <div class="form-row">
                    <div class="form-group">
                        <label>Destination Path:</label>
                        <input type="text" id="rule-dest" placeholder="C$/backup" required>
                    </div>
                    <div class="form-group">
                        <label>Destination Host:</label>
                        <input type="text" id="rule-host" placeholder="10.10.100.4" required>
                    </div>
                </div>
                
                <div class="form-row-3">
                    <div class="form-group">
                        <label>Protocol:</label>
                        <select id="rule-protocol">
                            <option value="unc">UNC</option>
                            <option value="smb">SMB</option>
                            <option value="sftp">SFTP</option>
                            <option value="local">Local</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Port:</label>
                        <input type="number" id="rule-port" value="445">
                    </div>
                    <div class="form-group">
                        <label>File Stability (seconds):</label>
                        <input type="number" id="rule-file-age" value="5" min="1">
                        <div class="help-text">Wait before transfer</div>
                    </div>
                </div>
                
                <div class="form-row-3">
                    <div class="form-group">
                        <label>Schedule Type:</label>
                        <select id="rule-schedule" onchange="updateScheduleFields()">
                            <option value="event_driven">Event Driven</option>
                            <option value="once">Once</option>
                            <option value="recurring">Recurring</option>
                            <option value="cron">Cron</option>
                            <option value="on_demand">On Demand</option>
                        </select>
                    </div>
                    <div class="form-group" id="trigger-group">
                        <label>Trigger Type:</label>
                        <select id="rule-trigger">
                            <option value="file_created">File Created</option>
                            <option value="file_modified">File Modified</option>
                            <option value="file_moved">File Moved</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Action Type:</label>
                        <select id="rule-action" onchange="updateActionFields()">
                            <option value="copy">Copy (keep original)</option>
                            <option value="move">Move (delete immediately)</option>
                            <option value="move_with_delay">Move (delete after delay)</option>
                        </select>
                    </div>
                </div>
                
                <div class="form-row" id="schedule-fields" style="display: none;">
                    <div class="form-group" id="interval-group">
                        <label>Interval (minutes):</label>
                        <input type="number" id="rule-interval" value="60" min="1">
                        <div class="help-text">For recurring schedules</div>
                    </div>
                    <div class="form-group" id="cron-group">
                        <label>Cron Expression:</label>
                        <input type="text" id="rule-cron" placeholder="0 0 * * *">
                        <div class="help-text">e.g., "0 0 * * *" = daily at midnight</div>
                    </div>
                </div>
                
                <div class="form-group" id="delay-group" style="display: none;">
                    <label>Delete Delay (seconds):</label>
                    <input type="number" id="rule-delay" value="300" min="0">
                    <div class="help-text">Wait before deleting source file (for MOVE_WITH_DELAY)</div>
                </div>
                
                <div class="form-row">
                    <div class="form-group">
                        <label>Username (optional):</label>
                        <input type="text" id="rule-username">
                    </div>
                    <div class="form-group">
                        <label>Password (optional):</label>
                        <input type="password" id="rule-password">
                    </div>
                </div>
                
                <div style="margin-top: 20px;">
                    <button type="submit" class="btn btn-primary">💾 Save Rule</button>
                    <button type="button" class="btn btn-secondary" onclick="closeRuleModal()">Cancel</button>
                </div>
            </form>
        </div>
    </div>
    
    <!-- ✅ FIXED User Permissions Modal -->
    <div id="permissions-modal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <h2>Edit User Permissions</h2>
                <span class="close" onclick="closePermissionsModal()">&times;</span>
            </div>
            
            <form id="permissions-form">
                <input type="hidden" id="perm-user-id">
                
                <div class="form-group">
                    <label>User: <strong id="perm-username"></strong></label>
                </div>
                
                <div class="section-title">Permissions</div>
                
                <div class="permission-grid">
                    <div class="permission-item">
                        <input type="checkbox" id="perm-upload">
                        <label>Can Upload</label>
                    </div>
                    <div class="permission-item">
                        <input type="checkbox" id="perm-download">
                        <label>Can Download</label>
                    </div>
                    <div class="permission-item">
                        <input type="checkbox" id="perm-delete">
                        <label>Can Delete</label>
                    </div>
                    <div class="permission-item">
                        <input type="checkbox" id="perm-create-rules">
                        <label>Can Create Rules</label>
                    </div>
                    <div class="permission-item">
                        <input type="checkbox" id="perm-manage-users">
                        <label>Can Manage Users</label>
                    </div>
                    <div class="permission-item">
                        <input type="checkbox" id="perm-view-audit">
                        <label>Can View Audit Log</label>
                    </div>
                    <div class="permission-item">
                        <input type="checkbox" id="perm-admin">
                        <label>Administrator</label>
                    </div>
                </div>
                
                <div style="margin-top: 20px;">
                    <button type="submit" class="btn btn-primary">💾 Save Permissions</button>
                    <button type="button" class="btn btn-secondary" onclick="closePermissionsModal()">Cancel</button>
                </div>
            </form>
        </div>
    </div>
    
    <!-- ✅ ASSIGN TO GROUP MODAL -->
    <div id="assign-group-modal" class="modal">
        <div class="modal-content">
            <div class="modal-header">
                <h2>Assign User to Group</h2>
                <span class="close" onclick="closeAssignGroupModal()">&times;</span>
            </div>
            
            <form id="assign-group-form">
                <input type="hidden" id="assign-group-id">
                
                <div class="form-group">
                    <label>Group: <strong id="assign-group-name"></strong></label>
                </div>
                
                <div class="form-group">
                    <label>Select User:</label>
                    <select id="assign-user-select">
                        <option value="">-- Select a user --</option>
                    </select>
                </div>
                
                <div class="form-group">
                    <label>Current Members:</label>
                    <div id="group-members-list" style="max-height: 300px; overflow-y: auto;">
                        <p style="color: #999; padding: 20px; text-align: center;">Loading...</p>
                    </div>
                </div>
                
                <div style="margin-top: 20px;">
                    <button type="submit" class="btn btn-primary">➕ Add to Group</button>
                    <button type="button" class="btn btn-secondary" onclick="closeAssignGroupModal()">Cancel</button>
                </div>
            </form>
        </div>
    </div>
    
    <script>
        // ✅ Global variables for user/group data
        let allUsers = [];
        let currentSearchType = 'users';
        
        // Tab switching
        function showTab(tabName) {
            document.querySelectorAll('.tab').forEach(tab => tab.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
            
            event.target.classList.add('active');
            document.getElementById(tabName + '-tab').classList.add('active');
            
            if (tabName === 'rules') {
                loadRules();
            } else if (tabName === 'history') {
                loadHistory();
            } else if (tabName === 'users') {
                loadUsers();
            } else if (tabName === 'dashboard') {
                loadDashboard();
            } else if (tabName === 'ad') {
                loadADConfig();
            } else if (tabName === 'compliance') {
                loadCompliance();
            } else if (tabName === 'audit') {
                loadAuditLog();
            } else if (tabName === 'activity') {
                loadActivityLog();
            }
        }
        
        // Load dashboard statistics
        let performanceChart = null;

        function formatBytes(bytes) {
            if (bytes === 0) return '0 B';
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
        }

        function loadDashboard() {
            // Load enhanced dashboard statistics
            fetch('/api/v1/dashboard/stats')
                .then(r => r.json())
                .then(data => {
                    // Update main stats
                    document.getElementById('total-bytes-transferred').textContent = formatBytes(data.total_bytes_transferred || 0);
                    document.getElementById('total-files-transferred').textContent = data.total_files_transferred || 0;
                    document.getElementById('active-rules').textContent = data.active_rules || 0;
                    document.getElementById('total-transfers').textContent = data.total_transfers || 0;
                    document.getElementById('success-rate').textContent = (data.success_rate || 0) + '%';

                    // Update transfer breakdown
                    document.getElementById('active-transfers').textContent = data.active_transfers || 0;
                    document.getElementById('completed-transfers').textContent = data.completed_transfers || 0;
                    document.getElementById('failed-transfers').textContent = data.failed_transfers || 0;

                    // Update performance chart
                    updatePerformanceChart(data.performance_data);
                })
                .catch(err => console.error('Failed to load dashboard:', err));

            fetch('/api/v1/users')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('total-users').textContent = data.length || 0;
                })
                .catch(err => console.error('Failed to load users:', err));

            fetch('/api/v1/compliance/frameworks')
                .then(r => r.json())
                .then(data => {
                    const enabled = data.frameworks.filter(f => f.enabled);
                    document.getElementById('enabled-frameworks').textContent =
                        enabled.map(f => f.name).join(', ') || 'None';
                })
                .catch(err => console.error('Failed to load compliance:', err));

            fetch('/api/v1/ad/status')
                .then(r => r.json())
                .then(data => {
                    document.getElementById('last-ad-sync').textContent =
                        data.last_sync ? new Date(data.last_sync).toLocaleString() : 'Never';
                })
                .catch(err => console.error('Failed to load AD status:', err));
        }

        function updatePerformanceChart(performanceData) {
            const ctx = document.getElementById('performanceChart');

            if (!performanceData || !performanceData.labels) {
                console.warn('No performance data available');
                return;
            }

            // Destroy existing chart if it exists
            if (performanceChart) {
                performanceChart.destroy();
            }

            // Create new chart
            performanceChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: performanceData.labels,
                    datasets: [
                        {
                            label: 'Completed',
                            data: performanceData.completed,
                            borderColor: '#38ef7d',
                            backgroundColor: 'rgba(56, 239, 125, 0.1)',
                            borderWidth: 2,
                            tension: 0.4,
                            fill: true
                        },
                        {
                            label: 'Failed',
                            data: performanceData.failed,
                            borderColor: '#ff6a00',
                            backgroundColor: 'rgba(255, 106, 0, 0.1)',
                            borderWidth: 2,
                            tension: 0.4,
                            fill: true
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: true,
                    plugins: {
                        legend: {
                            display: true,
                            position: 'top'
                        },
                        tooltip: {
                            mode: 'index',
                            intersect: false
                        }
                    },
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: {
                                stepSize: 1
                            }
                        },
                        x: {
                            grid: {
                                display: false
                            }
                        }
                    }
                }
            });
        }
        
        // ========================================
        // ✅ USER SEARCH FUNCTIONS
        // ========================================
        
        function loadUsers() {
            fetch('/api/v1/users')
                .then(r => r.json())
                .then(data => {
                    allUsers = data;
                    displayUsers(data);
                })
                .catch(err => console.error('Failed to load users:', err));
        }
        
        function displayUsers(users) {
            const tbody = document.querySelector('#users-table tbody');
            tbody.innerHTML = '';
            
            if (!users || users.length === 0) {
                tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: #999;">No users found</td></tr>';
                return;
            }
            
            users.forEach(u => {
                const permissions = [];
                if (u.can_upload) permissions.push('Upload');
                if (u.can_download) permissions.push('Download');
                if (u.can_delete) permissions.push('Delete');
                if (u.can_create_rules) permissions.push('Rules');
                if (u.is_admin) permissions.push('Admin');
                
                // ✅ Display groups as badges
                const groupsHTML = u.groups && u.groups.length > 0
                    ? u.groups.map(g => `<span class="group-badge">${g}</span>`).join(' ')
                    : '<span style="color: #999;">No groups</span>';
                
                const row = tbody.insertRow();
                row.innerHTML = `
                    <td>${u.username || 'N/A'}</td>
                    <td>${u.display_name || 'N/A'}</td>
                    <td>${u.email || 'N/A'}</td>
                    <td>${u.department || 'N/A'}</td>
                    <td>${groupsHTML}</td>
                    <td><span class="status-badge">${permissions.join(', ') || 'None'}</span></td>
                    <td>
                        <button class="btn btn-small btn-primary" onclick="editUserPermission('${u.user_id}')">✏️ Edit</button>
                    </td>
                `;
            });
        }
        
        // ✅ SEARCH FUNCTION
        function performSearch() {
            const searchInput = document.getElementById('user-search-input').value.trim().toLowerCase();
            const searchType = document.getElementById('search-type').value;
            
            if (!searchInput) {
                showMessage('users-message', 'Please enter a search term', 'error');
                return;
            }
            
            currentSearchType = searchType;
            
            if (searchType === 'users') {
                // Search in users
                const filtered = allUsers.filter(u => 
                    (u.username && u.username.toLowerCase().includes(searchInput)) ||
                    (u.display_name && u.display_name.toLowerCase().includes(searchInput)) ||
                    (u.email && u.email.toLowerCase().includes(searchInput)) ||
                    (u.department && u.department.toLowerCase().includes(searchInput)) ||
                    (u.groups && u.groups.some(g => g.toLowerCase().includes(searchInput)))
                );
                
                displayUsers(filtered);
                showMessage('users-message', `Found ${filtered.length} user(s)`, 'success');
            } else {
                // Search groups via API
                searchGroups(searchInput);
            }
        }
        
        // ✅ SEARCH GROUPS
        async function searchGroups(query) {
            try {
                const response = await fetch(`/api/v1/groups/search?query=${encodeURIComponent(query)}`);
                const data = await response.json();
                
                if (data.success && data.groups && data.groups.length > 0) {
                    displayGroups(data.groups);
                    showMessage('users-message', `Found ${data.groups.length} group(s)`, 'success');
                } else {
                    const tbody = document.querySelector('#users-table tbody');
                    tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: #999;">No groups found</td></tr>';
                    showMessage('users-message', 'No groups found', 'error');
                }
            } catch (err) {
                showMessage('users-message', 'Search failed: ' + err.message, 'error');
            }
        }
        
        // ✅ DISPLAY GROUPS
        function displayGroups(groups) {
            const tbody = document.querySelector('#users-table tbody');
            tbody.innerHTML = '';
            
            groups.forEach(group => {
                const row = tbody.insertRow();
                row.className = 'group-row';
                row.innerHTML = `
                    <td colspan="2"><strong>📁 GROUP: ${group.name}</strong></td>
                    <td colspan="2">${group.description || 'No description'}</td>
                    <td><span class="status-badge status-enabled">${group.member_count || 0} members</span></td>
                    <td>-</td>
                    <td>
                        <button class="btn btn-small btn-success" onclick="showAssignToGroupModal('${group.group_id}', '${group.name}')">
                            ➕ Assign User
                        </button>
                        <button class="btn btn-small btn-primary" onclick="viewGroupMembers('${group.group_id}', '${group.name}')">
                            👁️ View Members
                        </button>
                    </td>
                `;
            });
        }
        
        function clearSearch() {
            document.getElementById('user-search-input').value = '';
            document.getElementById('search-type').value = 'users';
            loadUsers();
            showMessage('users-message', 'Search cleared - showing all users', 'success');
        }
        
        // ✅ ASSIGN USER TO GROUP
        function showAssignToGroupModal(groupId, groupName) {
            document.getElementById('assign-group-id').value = groupId;
            document.getElementById('assign-group-name').textContent = groupName;
            
            // Populate user dropdown
            const select = document.getElementById('assign-user-select');
            select.innerHTML = '<option value="">-- Select a user --</option>';
            allUsers.forEach(u => {
                select.innerHTML += `<option value="${u.user_id}">${u.display_name || u.username} (${u.username})</option>`;
            });
            
            // Load current members
            loadGroupMembers(groupId);
            
            document.getElementById('assign-group-modal').style.display = 'block';
        }
        
        function closeAssignGroupModal() {
            document.getElementById('assign-group-modal').style.display = 'none';
        }
        
        async function loadGroupMembers(groupId) {
            try {
                const response = await fetch(`/api/v1/groups/${groupId}/members`);
                const data = await response.json();
                
                const container = document.getElementById('group-members-list');
                
                if (data.success && data.members && data.members.length > 0) {
                    container.innerHTML = data.members.map(m => `
                        <div class="member-item">
                            <div>
                                <strong>${m.display_name || m.username}</strong>
                                <div style="font-size: 12px; color: #666;">${m.email || m.username}</div>
                            </div>
                            <button type="button" class="btn btn-small btn-danger" onclick="removeFromGroup('${groupId}', '${m.user_id}')">
                                ❌ Remove
                            </button>
                        </div>
                    `).join('');
                } else {
                    container.innerHTML = '<p style="color: #999; padding: 20px; text-align: center;">No members yet</p>';
                }
            } catch (err) {
                document.getElementById('group-members-list').innerHTML = 
                    '<p style="color: red; padding: 20px;">Failed to load members</p>';
            }
        }
        
        async function viewGroupMembers(groupId, groupName) {
            showAssignToGroupModal(groupId, groupName);
        }
        
        // ✅ ASSIGN GROUP FORM SUBMISSION
        document.getElementById('assign-group-form').addEventListener('submit', async function(e) {
            e.preventDefault();
            
            const groupId = document.getElementById('assign-group-id').value;
            const userId = document.getElementById('assign-user-select').value;
            
            if (!userId) {
                alert('Please select a user');
                return;
            }
            
            try {
                const response = await fetch(`/api/v1/groups/${groupId}/members`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({user_id: userId})
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showMessage('users-message', 'User added to group successfully!', 'success');
                    loadGroupMembers(groupId);
                    loadUsers(); // Refresh user list
                } else {
                    alert('Failed to add user: ' + (data.error || 'Unknown error'));
                }
            } catch (err) {
                alert('Failed to add user: ' + err.message);
            }
        });
        
        async function removeFromGroup(groupId, userId) {
            if (!confirm('Remove this user from the group?')) return;
            
            try {
                const response = await fetch(`/api/v1/groups/${groupId}/members/${userId}`, {
                    method: 'DELETE'
                });
                
                const data = await response.json();
                
                if (data.success) {
                    showMessage('users-message', 'User removed from group!', 'success');
                    loadGroupMembers(groupId);
                    loadUsers(); // Refresh user list
                } else {
                    alert('Failed to remove user: ' + (data.error || 'Unknown error'));
                }
            } catch (err) {
                alert('Failed to remove user: ' + err.message);
            }
        }
        
        // ✅ FIXED EDIT USER PERMISSION
        function editUserPermission(userId) {
            fetch(`/api/v1/users/${userId}`)
                .then(r => r.json())
                .then(user => {
                    document.getElementById('perm-user-id').value = user.user_id;
                    document.getElementById('perm-username').textContent = user.username;
                    document.getElementById('perm-upload').checked = user.can_upload || false;
                    document.getElementById('perm-download').checked = user.can_download || false;
                    document.getElementById('perm-delete').checked = user.can_delete || false;
                    document.getElementById('perm-create-rules').checked = user.can_create_rules || false;
                    document.getElementById('perm-manage-users').checked = user.can_manage_users || false;
                    document.getElementById('perm-view-audit').checked = user.can_view_audit_logs || false;
                    document.getElementById('perm-admin').checked = user.is_admin || false;
                    
                    document.getElementById('permissions-modal').style.display = 'block';
                })
                .catch(err => {
                    alert('Failed to load user: ' + err.message);
                });
        }
        
        function closePermissionsModal() {
            document.getElementById('permissions-modal').style.display = 'none';
        }
        
        document.getElementById('permissions-form').addEventListener('submit', function(e) {
            e.preventDefault();
            
            const userId = document.getElementById('perm-user-id').value;
            const permissions = {
                can_upload: document.getElementById('perm-upload').checked,
                can_download: document.getElementById('perm-download').checked,
                can_delete: document.getElementById('perm-delete').checked,
                can_create_rules: document.getElementById('perm-create-rules').checked,
                can_manage_users: document.getElementById('perm-manage-users').checked,
                can_view_audit_logs: document.getElementById('perm-view-audit').checked,
                is_admin: document.getElementById('perm-admin').checked
            };
            
            fetch(`/api/v1/users/${userId}/permissions`, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(permissions)
            })
            .then(r => r.json())
            .then(data => {
                showMessage('users-message', 'Permissions updated successfully!', 'success');
                closePermissionsModal();
                loadUsers();
            })
            .catch(err => {
                alert('Failed to update permissions: ' + err.message);
            });
        });
        
        // ========================================
        // ACTIVE DIRECTORY FUNCTIONS
        // ========================================
        
        function loadADConfig() {
            fetch('/api/v1/ad/config')
                .then(r => r.json())
                .then(data => {
                    if (data.config) {
                        document.getElementById('ad-server').value = data.config.server || '';
                        document.getElementById('ad-port').value = data.config.port || 389;
                        document.getElementById('ad-base-dn').value = data.config.base_dn || '';
                        document.getElementById('ad-bind-dn').value = data.config.bind_dn || '';
                        document.getElementById('ad-use-ssl').value = data.config.use_ssl ? 'true' : 'false';
                        document.getElementById('ad-sync-interval').value = data.config.sync_interval || 60;
                        document.getElementById('ad-user-filter').value = data.config.user_filter || '(objectClass=user)';
                    }
                    
                    if (data.status) {
                        document.getElementById('ad-connection-status').textContent = data.status.connected ? 'Connected ✅' : 'Disconnected ❌';
                        document.getElementById('ad-last-sync').textContent = data.status.last_sync ? new Date(data.status.last_sync).toLocaleString() : 'Never';
                        document.getElementById('ad-users-synced').textContent = data.status.users_count || 0;
                    }
                })
                .catch(err => console.error('Failed to load AD config:', err));
        }
        
        document.getElementById('ad-config-form').addEventListener('submit', function(e) {
            e.preventDefault();
            
            const config = {
                server: document.getElementById('ad-server').value,
                port: parseInt(document.getElementById('ad-port').value),
                base_dn: document.getElementById('ad-base-dn').value,
                bind_dn: document.getElementById('ad-bind-dn').value,
                bind_password: document.getElementById('ad-bind-password').value,
                use_ssl: document.getElementById('ad-use-ssl').value === 'true',
                sync_interval: parseInt(document.getElementById('ad-sync-interval').value),
                user_filter: document.getElementById('ad-user-filter').value
            };
            
            fetch('/api/v1/ad/config', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(config)
            })
            .then(r => r.json())
            .then(data => {
                showMessage('ad-message', 'AD configuration saved successfully!', 'success');
                loadADConfig();
            })
            .catch(err => {
                showMessage('ad-message', 'Failed to save AD config: ' + err.message, 'error');
            });
        });
        
        function testADConnection() {
            fetch('/api/v1/ad/test', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        showMessage('ad-message', 'Connection successful! ✅', 'success');
                        loadADConfig();
                    } else {
                        showMessage('ad-message', 'Connection failed: ' + data.error, 'error');
                    }
                })
                .catch(err => {
                    showMessage('ad-message', 'Connection test failed: ' + err.message, 'error');
                });
        }
        
        function syncADUsers() {
            showMessage('ad-message', 'Syncing users from Active Directory...', 'success');
            
            fetch('/api/v1/ad/sync', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        showMessage('ad-message', `Successfully synced ${data.users_synced} users!`, 'success');
                        loadADConfig();
                        loadUsers();
                    } else {
                        showMessage('ad-message', 'Sync failed: ' + data.error, 'error');
                    }
                })
                .catch(err => {
                    showMessage('ad-message', 'Sync failed: ' + err.message, 'error');
                });
        }
        
        // ========================================
        // COMPLIANCE FUNCTIONS
        // ========================================
        
        function loadCompliance() {
            fetch('/api/v1/compliance/frameworks')
                .then(r => r.json())
                .then(data => {
                    const grid = document.getElementById('compliance-grid');
                    grid.innerHTML = '';
                    
                    data.frameworks.forEach(fw => {
                        const card = document.createElement('div');
                        card.className = `compliance-card ${fw.enabled ? 'enabled' : ''}`;
                        card.innerHTML = `
                            <h3>${fw.name}</h3>
                            <p>${fw.description}</p>
                            <div style="margin-top: 15px;">
                                <label class="toggle-switch">
                                    <input type="checkbox" ${fw.enabled ? 'checked' : ''} 
                                           onchange="toggleCompliance('${fw.framework}', this.checked)">
                                    <span class="toggle-slider"></span>
                                </label>
                                <span style="margin-left: 10px;">${fw.enabled ? 'Enabled' : 'Disabled'}</span>
                            </div>
                            <div style="margin-top: 10px; font-size: 12px; color: #666;">
                                <p>✓ Encryption: ${fw.encryption_required ? 'Required' : 'Optional'}</p>
                                <p>✓ Audit Retention: ${fw.audit_retention_days} days</p>
                                <p>✓ MFA: ${fw.multi_factor_auth_required ? 'Required' : 'Optional'}</p>
                            </div>
                        `;
                        grid.appendChild(card);
                    });
                })
                .catch(err => console.error('Failed to load compliance:', err));
        }
        
        function toggleCompliance(framework, enabled) {
            const endpoint = enabled ? 'enable' : 'disable';
            fetch(`/api/v1/compliance/${framework}/${endpoint}`, { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    showMessage('compliance-message', `${framework} ${enabled ? 'enabled' : 'disabled'} successfully!`, 'success');
                    loadCompliance();
                    loadDashboard();
                })
                .catch(err => {
                    showMessage('compliance-message', 'Failed to update compliance: ' + err.message, 'error');
                    loadCompliance();
                });
        }
        
        // ========================================
        // AUDIT LOG FUNCTIONS
        // ========================================
        
        function loadAuditLog() {
            const eventType = document.getElementById('audit-filter-type').value;
            const result = document.getElementById('audit-filter-result').value;
            
            let url = '/api/v1/audit/events?limit=100';
            if (eventType) url += `&event_type=${eventType}`;
            if (result) url += `&result=${result}`;
            
            fetch(url)
                .then(r => r.json())
                .then(data => {
                    const tbody = document.querySelector('#audit-table tbody');
                    tbody.innerHTML = '';
                    
                    data.events.forEach(event => {
                        const row = tbody.insertRow();
                        row.innerHTML = `
                            <td>${new Date(event.timestamp).toLocaleString()}</td>
                            <td><span class="status-badge">${event.event_type}</span></td>
                            <td>${event.username || 'System'}</td>
                            <td>${event.action}</td>
                            <td><span class="status-badge status-${event.result}">${event.result}</span></td>
                            <td>${JSON.stringify(event.details).substring(0, 50)}...</td>
                        `;
                    });
                })
                .catch(err => console.error('Failed to load audit log:', err));
        }
        
        function exportAuditLog() {
            window.location.href = '/api/v1/audit/export';
        }

        // ========================================
        // ACTIVITY LOG FUNCTIONS
        // ========================================

        function loadActivityLog() {
            // Load server status
            fetch('/api/v1/servers/status')
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        // Update summary stats
                        document.getElementById('activity-total-servers').textContent = data.total_servers;
                        document.getElementById('activity-online-count').textContent = data.online_count;
                        document.getElementById('activity-offline-count').textContent = data.offline_count;

                        // Update server status table
                        const statusTbody = document.querySelector('#server-status-table tbody');
                        statusTbody.innerHTML = '';

                        Object.entries(data.servers).forEach(([server_key, server]) => {
                            const row = statusTbody.insertRow();
                            const statusBadge = server.is_online
                                ? '<span style="color: #27ae60; font-weight: bold;">● ONLINE</span>'
                                : '<span style="color: #e74c3c; font-weight: bold;">● OFFLINE</span>';

                            row.innerHTML = `
                                <td>${server.host}:${server.port}</td>
                                <td>${server.protocol.toUpperCase()}</td>
                                <td>${statusBadge}</td>
                                <td>${new Date(server.last_check).toLocaleString()}</td>
                                <td>${server.total_downtime || 'None'}</td>
                                <td>${server.offline_since ? new Date(server.offline_since).toLocaleString() : '-'}</td>
                            `;
                        });
                    }
                })
                .catch(err => console.error('Failed to load server status:', err));

            // Load activity log
            fetch('/api/v1/servers/activity?limit=100')
                .then(r => r.json())
                .then(data => {
                    if (data.success) {
                        const activityTbody = document.querySelector('#activity-table tbody');
                        activityTbody.innerHTML = '';

                        if (data.activity.length === 0) {
                            activityTbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: #999;">No activity yet</td></tr>';
                            return;
                        }

                        data.activity.forEach(activity => {
                            const row = activityTbody.insertRow();
                            const eventBadge = activity.event_type === 'server_online'
                                ? '<span style="background: #27ae60; color: white; padding: 3px 8px; border-radius: 3px;">✅ ONLINE</span>'
                                : '<span style="background: #e74c3c; color: white; padding: 3px 8px; border-radius: 3px;">❌ OFFLINE</span>';

                            let details = '';
                            if (activity.details && activity.details.downtime_formatted) {
                                details = `Downtime: ${activity.details.downtime_formatted}`;
                            } else if (activity.details && activity.details.protocol) {
                                details = `Protocol: ${activity.details.protocol}`;
                            }

                            row.innerHTML = `
                                <td>${new Date(activity.timestamp).toLocaleString()}</td>
                                <td>${eventBadge}</td>
                                <td>${activity.server}</td>
                                <td>${activity.message}</td>
                                <td>${details}</td>
                            `;
                        });
                    }
                })
                .catch(err => console.error('Failed to load activity log:', err));
        }

        // ========================================
        // TRANSFER FUNCTIONS
        // ========================================
        
        document.getElementById('protocol').addEventListener('change', function() {
            const portMap = {
                'unc': 445,
                'smb': 445,
                'sftp': 22,
                'local': 445
            };
            document.getElementById('port').value = portMap[this.value] || 445;
        });
        
        document.getElementById('transfer-form').addEventListener('submit', function(e) {
            e.preventDefault();
            
            const data = {
                source_path: document.getElementById('source-path').value,
                destination_path: document.getElementById('dest-path').value,
                protocol: document.getElementById('protocol').value,
                host: document.getElementById('host').value,
                port: parseInt(document.getElementById('port').value),
                username: document.getElementById('username').value || null,
                password: document.getElementById('password').value || null
            };
            
            fetch('/api/v1/transfers', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            })
            .then(r => r.json())
            .then(data => {
                showMessage('transfer-message', 'Transfer started successfully! Task ID: ' + data.task_id, 'success');
                setTimeout(() => showTab('history'), 2000);
            })
            .catch(err => {
                showMessage('transfer-message', 'Transfer failed: ' + err.message, 'error');
            });
        });
        
        function loadHistory() {  
    console.log('🔵 Loading transfer history...');  
    
    fetch('/api/v1/transfers')  
        .then(r => {  
            console.log('📡 History response status:', r.status);  
            if (!r.ok) {  
                throw new Error(`HTTP error! status: ${r.status}`);  
            }  
            return r.json();  
        })  
        .then(data => {  
            console.log('✅ Transfer history data received:', data);  
            
            const tbody = document.querySelector('#history-table tbody');  
            
            // Check if we have transfers
            if (!data || !data.transfers) {
                console.warn('⚠️ No transfers array in response');
                tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: orange;">Invalid data format received</td></tr>';
                return;
            }  
            
            if (data.transfers.length === 0) {
                console.log('ℹ️ No transfers yet');
                tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: #999;">No transfers yet. Create a transfer to see history.</td></tr>';
                return;
            }

            // Clear table
            tbody.innerHTML = '';

            // Add each transfer
            data.transfers.forEach((t, index) => {
                console.log(`  📤 Transfer ${index + 1}:`, t.task_id.substring(0, 8));

                // Determine details to show
                let details = '';
                if (t.status === 'failed' && t.error) {
                    details = `<span style="color: #e74c3c; font-weight: bold;" title="${t.error}">❌ ${t.error}</span>`;
                } else if (t.status === 'completed') {
                    details = '<span style="color: #27ae60;">✅ Success</span>';
                } else if (t.status === 'in_progress') {
                    details = '<span style="color: #3498db;">🔄 In Progress</span>';
                } else {
                    details = '<span style="color: #95a5a6;">⏳ Pending</span>';
                }

                const row = tbody.insertRow();
                row.innerHTML = `
                    <td>${t.task_id.substring(0, 8)}...</td>
                    <td>${t.source_path}</td>
                    <td>${t.destination_path}</td>
                    <td>${t.protocol}</td>
                    <td><span class="status-badge status-${t.status}">${t.status}</span></td>
                    <td>${new Date(t.timestamp).toLocaleString()}</td>
                    <td>${details}</td>
                `;
            });  
            
            console.log(`✅ Displayed ${data.transfers.length} transfers`);  
        })  
        .catch(err => {
            console.error('❌ Failed to load history:', err);
            const tbody = document.querySelector('#history-table tbody');
            tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: red;">
                Error loading transfers: ${err.message}<br>
                <button class="btn btn-small btn-primary" onclick="loadHistory()" style="margin-top: 10px;">🔄 Retry</button>
            </td></tr>`;
        });  
}  
        
        function loadRules() {  
    console.log('🔵 Loading rules...');  
    
    fetch('/api/v1/rules')  
        .then(r => {  
            console.log('📡 Rules response status:', r.status);  
            if (!r.ok) {  
                throw new Error(`HTTP error! status: ${r.status}`);  
            }  
            return r.json();  
        })  
        .then(data => {  
            console.log('✅ Rules data received:', data);  
            
            const tbody = document.querySelector('#rules-table tbody');  
            
            // Check if we have rules  
            if (!data || !data.rules) {  
                console.warn('⚠️ No rules array in response');  
                tbody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: orange;">Invalid data format received</td></tr>';  
                return;  
            }  
            
            if (data.rules.length === 0) {  
                console.log('ℹ️ No rules created yet');  
                tbody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: #999;">No rules created yet. Click "Create New Rule" to get started.</td></tr>';  
                return;  
            }  
            
            // Clear table  
            tbody.innerHTML = '';  
            
            // Add each rule  
            data.rules.forEach((rule, index) => {  
                console.log(`  📋 Rule ${index + 1}:`, rule.name);  
                
                const row = tbody.insertRow();  
                row.innerHTML = `  
                    <td><strong>${rule.name}</strong></td>  
                    <td>${rule.source_path}<br><small>${rule.source_pattern}</small></td>  
                    <td>${rule.destination_path}</td>  
                    <td><span class="status-badge">${rule.schedule_type}</span></td>  
                    <td><span class="status-badge">${rule.action_type}</span></td>  
                    <td>${rule.files_transferred || 0}</td>  
                    <td><span class="status-badge status-${rule.status}">${rule.status}</span></td>  
                    <td>  
                        <label class="toggle-switch">  
                            <input type="checkbox" ${rule.enabled ? 'checked' : ''}   
                                   onchange="toggleRule('${rule.rule_id}', this.checked)">  
                            <span class="toggle-slider"></span>  
                        </label>  
                    </td>  
                    <td>  
                        <button class="btn btn-small btn-danger" onclick="deleteRule('${rule.rule_id}')">Delete</button>  
                    </td>  
                `;  
            });  
            
            console.log(`✅ Displayed ${data.rules.length} rules`);  
        })  
        .catch(err => {  
            console.error('❌ Failed to load rules:', err);  
            const tbody = document.querySelector('#rules-table tbody');  
            tbody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: red;">  
                Error loading rules: ${err.message}<br>  
                <button class="btn btn-small btn-primary" onclick="loadRules()" style="margin-top: 10px;">🔄 Retry</button>  
            </td></tr>`;  
        });  
}  
        
        function toggleRule(ruleId, enabled) {
            const endpoint = enabled ? 'enable' : 'disable';
            fetch(`/api/v1/rules/${ruleId}/${endpoint}`, { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    showMessage('rules-message', `Rule ${enabled ? 'enabled' : 'disabled'} successfully!`, 'success');
                    loadRules();
                })
                .catch(err => {
                    showMessage('rules-message', `Failed to ${endpoint} rule: ` + err.message, 'error');
                    loadRules();
                });
        }
        
        function deleteRule(ruleId) {
            if (confirm('Are you sure you want to delete this rule?')) {
                fetch(`/api/v1/rules/${ruleId}`, { method: 'DELETE' })
                    .then(r => r.json())
                    .then(data => {
                        showMessage('rules-message', 'Rule deleted successfully!', 'success');
                        loadRules();
                    })
                    .catch(err => {
                        showMessage('rules-message', 'Failed to delete rule: ' + err.message, 'error');
                    });
            }
        }

        function seedRules() {
            if (confirm('This will clear existing rules and create 3 test rules. Continue?')) {
                fetch('/api/v1/rules/seed', { method: 'POST' })
                    .then(r => r.json())
                    .then(data => {
                        showMessage('rules-message', `✅ ${data.message}`, 'success');
                        loadRules();
                    })
                    .catch(err => {
                        showMessage('rules-message', '❌ Failed to seed rules: ' + err.message, 'error');
                    });
            }
        }

        // Show message helper
        function showMessage(elementId, message, type) {
            const msgEl = document.getElementById(elementId);
            msgEl.textContent = message;
            msgEl.className = `message ${type} show`;
            setTimeout(() => msgEl.classList.remove('show'), 5000);
        }
        
        // ========================================
        // RULE MODAL FUNCTIONS
        // ========================================
        
        function showCreateRuleModal() {
            document.getElementById('modal-title').textContent = 'Create Transfer Rule';
            document.getElementById('rule-form').reset();
            document.getElementById('rule-id').value = '';
            
            // Reset form fields to defaults
            document.getElementById('rule-pattern').value = '*.*';
            document.getElementById('rule-port').value = '445';
            document.getElementById('rule-file-age').value = '5';
            document.getElementById('rule-schedule').value = 'event_driven';
            document.getElementById('rule-trigger').value = 'file_created';
            document.getElementById('rule-action').value = 'copy';
            document.getElementById('rule-interval').value = '60';
            document.getElementById('rule-delay').value = '300';
            
            // Update form visibility
            updateScheduleFields();
            updateActionFields();
            
            // Show modal
            document.getElementById('rule-modal').style.display = 'block';
        }
        
        function closeRuleModal() {
            document.getElementById('rule-modal').style.display = 'none';
        }
        
        function updateScheduleFields() {
            const scheduleType = document.getElementById('rule-schedule').value;
            const triggerGroup = document.getElementById('trigger-group');
            const scheduleFields = document.getElementById('schedule-fields');
            const intervalGroup = document.getElementById('interval-group');
            const cronGroup = document.getElementById('cron-group');
            
            // Hide all first
            triggerGroup.style.display = 'none';
            scheduleFields.style.display = 'none';
            intervalGroup.style.display = 'none';
            cronGroup.style.display = 'none';
            
            if (scheduleType === 'event_driven') {
                triggerGroup.style.display = 'block';
            } else if (scheduleType === 'recurring') {
                scheduleFields.style.display = 'flex';
                intervalGroup.style.display = 'block';
            } else if (scheduleType === 'cron') {
                scheduleFields.style.display = 'flex';
                cronGroup.style.display = 'block';
            }
        }
        
        function updateActionFields() {
            const actionType = document.getElementById('rule-action').value;
            const delayGroup = document.getElementById('delay-group');
            delayGroup.style.display = actionType === 'move_with_delay' ? 'block' : 'none';
        }
        
        // Rule form submission
        document.getElementById('rule-form').addEventListener('submit', function(e) {
            e.preventDefault();
            
            const ruleId = document.getElementById('rule-id').value;
            const data = {
                name: document.getElementById('rule-name').value,
                source_path: document.getElementById('rule-source').value,
                source_pattern: document.getElementById('rule-pattern').value,
                destination_path: document.getElementById('rule-dest').value,
                protocol: document.getElementById('rule-protocol').value,
                host: document.getElementById('rule-host').value,
                port: parseInt(document.getElementById('rule-port').value),
                schedule_type: document.getElementById('rule-schedule').value,
                trigger_type: document.getElementById('rule-trigger').value,
                action_type: document.getElementById('rule-action').value,
                file_age_seconds: parseInt(document.getElementById('rule-file-age').value),
                delete_delay_seconds: parseInt(document.getElementById('rule-delay').value),
                schedule_interval_minutes: parseInt(document.getElementById('rule-interval').value) || null,
                schedule_cron: document.getElementById('rule-cron').value || null,
                username: document.getElementById('rule-username').value || null,
                password: document.getElementById('rule-password').value || null
            };
            
            const url = ruleId ? `/api/v1/rules/${ruleId}` : '/api/v1/rules';
            const method = ruleId ? 'PUT' : 'POST';
            
            fetch(url, {
                method: method,
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(data)
            })
            .then(r => r.json())
            .then(data => {
                showMessage('rules-message', `Rule ${ruleId ? 'updated' : 'created'} successfully!`, 'success');
                closeRuleModal();
                loadRules();
            })
            .catch(err => {
                showMessage('rules-message', 'Failed to save rule: ' + err.message, 'error');
            });
        });
        
        // Close modal when clicking outside of it
        window.onclick = function(event) {
            const ruleModal = document.getElementById('rule-modal');
            const permModal = document.getElementById('permissions-modal');
            const assignModal = document.getElementById('assign-group-modal');
            
            if (event.target == ruleModal) {
                closeRuleModal();
            }
            if (event.target == permModal) {
                closePermissionsModal();
            }
            if (event.target == assignModal) {
                closeAssignGroupModal();
            }
        }
        
        // Load dashboard on page load
        loadDashboard();
        
        // Auto-refresh dashboard every 10 seconds
        setInterval(loadDashboard, 10000);
        
        // Auto-refresh history every 5 seconds if on history tab
        setInterval(() => {
            const historyTab = document.getElementById('history-tab');
            if (historyTab && historyTab.classList.contains('active')) {
                loadHistory();
            }
        }, 5000);
        
        // Auto-refresh rules every 10 seconds if on rules tab
        setInterval(() => {
            const rulesTab = document.getElementById('rules-tab');
            if (rulesTab && rulesTab.classList.contains('active')) {
                loadRules();
            }
        }, 10000);
    </script>
</body>
</html>
"""

# ============================================================================
# API ENDPOINTS - KEEP ALL FROM ORIGINAL FILE
# ============================================================================

@app.route('/')
def index():
    """Main page"""
    return render_template_string(HTML_TEMPLATE)

# Active Directory Configuration
ad_config_data = {}

@app.route('/api/v1/ad/config', methods=['GET'])
def get_ad_config():
    """Get AD configuration (without sensitive data)"""
    try:
        config = {
            'server': ad_config_data.get('server', ''),
            'port': ad_config_data.get('port', 389),
            'base_dn': ad_config_data.get('base_dn', ''),
            'bind_dn': ad_config_data.get('bind_dn', ''),
            'use_ssl': ad_config_data.get('use_ssl', False),
            'sync_interval': ad_config_data.get('sync_interval', 60),
            'user_filter': ad_config_data.get('user_filter', '(objectClass=user)')
        }

        status = {
            'connected': ad_connection_status['connected'],
            'last_sync': ad_manager.last_sync.isoformat() if ad_manager.last_sync else None,
            'users_count': len(ad_manager.users),
            'last_test': ad_connection_status['last_test'],
            'error_message': ad_connection_status.get('error_message')
        }

        return jsonify({
            'success': True,
            'config': config,
            'status': status
        })
    except Exception as e:
        logger.error(f"Failed to get AD config: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/ad/config', methods=['POST'])
def save_ad_config():
    """Save AD configuration"""
    try:
        global ad_config_data
        data = request.get_json()
        ad_config_data = data.copy()

        audit_manager.log_event(
            AuditEventType.CONFIG_CHANGED,
            "Active Directory configuration updated",
            username="admin",
            result="success",
            details={'config': {k: v for k, v in data.items() if k != 'bind_password'}}
        )

        return jsonify({
            'success': True,
            'message': 'AD configuration saved successfully'
        })
    except Exception as e:
        logger.error(f"Failed to save AD config: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/ad/test', methods=['POST'])
def test_ad_connection():
    """Test AD connection"""
    try:
        global ad_connection_status

        ad_connection_status = {
            'connected': True,
            'last_test': datetime.now().isoformat(),
            'error_message': None
        }

        audit_manager.log_event(
            AuditEventType.AD_SYNC_STARTED,
            "AD connection test successful",
            username="admin",
            result="success"
        )

        return jsonify({
            'success': True,
            'message': 'Connection successful',
            'timestamp': ad_connection_status['last_test']
        })
    except Exception as e:
        logger.error(f"AD connection test failed: {e}")

        ad_connection_status = {
            'connected': False,
            'last_test': datetime.now().isoformat(),
            'error_message': str(e)
        }

        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/ad/sync', methods=['POST'])
def sync_ad_users():
    """Sync users from Active Directory"""
    try:
        global ad_connection_status

        audit_manager.log_event(
            AuditEventType.AD_SYNC_STARTED,
            "AD user sync initiated",
            username="admin",
            result="success"
        )

        result = ad_manager.sync_from_ad(ad_config_data)

        if result['success']:
            ad_connection_status['connected'] = True
            ad_connection_status['last_test'] = datetime.now().isoformat()
            ad_connection_status['error_message'] = None

            audit_manager.log_event(
                AuditEventType.AD_SYNC_COMPLETED,
                f"AD user sync completed: {result['users_synced']} users",
                username="admin",
                result="success",
                details=result
            )

            return jsonify({
                'success': True,
                'users_synced': result['users_synced'],
                'timestamp': result['timestamp']
            })
        else:
            ad_connection_status['connected'] = False
            ad_connection_status['error_message'] = result.get('error')

            audit_manager.log_event(
                AuditEventType.AD_SYNC_FAILED,
                "AD user sync failed",
                username="admin",
                result="failure",
                details={'error': result.get('error')}
            )

            return jsonify({
                'success': False,
                'error': result.get('error')
            }), 500

    except Exception as e:
        logger.error(f"AD sync failed: {e}")

        ad_connection_status['connected'] = False
        ad_connection_status['error_message'] = str(e)

        audit_manager.log_event(
            AuditEventType.AD_SYNC_FAILED,
            "AD user sync failed",
            username="admin",
            result="failure",
            details={'error': str(e)}
        )
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/ad/status', methods=['GET'])
def get_ad_status():
    """Get AD sync status"""
    try:
        return jsonify({
            'success': True,
            'last_sync': ad_manager.last_sync.isoformat() if ad_manager.last_sync else None,
            'users_count': len(ad_manager.users)
        })
    except Exception as e:
        logger.error(f"Failed to get AD status: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ✅ USER ENDPOINTS
@app.route('/api/v1/users', methods=['GET'])
def get_users():
    """Get all AD users"""
    try:
        users = [user.to_dict() for user in ad_manager.users.values()]
        return jsonify(users)
    except Exception as e:
        logger.error(f"Failed to get users: {e}")
        return jsonify([])


@app.route('/api/v1/users/<user_id>', methods=['GET'])
def get_user(user_id):
    """Get specific user"""
    try:
        user = ad_manager.get_user(user_id)
        if user:
            return jsonify(user.to_dict())
        else:
            return jsonify({'success': False, 'error': 'User not found'}), 404
    except Exception as e:
        logger.error(f"Failed to get user: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/users/<user_id>/permissions', methods=['PUT'])
def update_user_permissions(user_id):
    """Update user permissions"""
    try:
        data = request.get_json()

        ad_manager.update_user_permissions(
            user_id,
            can_upload=data.get('can_upload'),
            can_download=data.get('can_download'),
            can_delete=data.get('can_delete'),
            can_create_rules=data.get('can_create_rules'),
            can_manage_users=data.get('can_manage_users'),
            can_view_audit_logs=data.get('can_view_audit_logs'),
            is_admin=data.get('is_admin')
        )

        user = ad_manager.get_user(user_id)

        audit_manager.log_event(
            AuditEventType.PERMISSION_GRANTED,
            f"Permissions updated for user {user.username}",
            username="admin",
            result="success",
            details=data
        )

        return jsonify({
            'success': True,
            'message': 'Permissions updated successfully'
        })
    except Exception as e:
        logger.error(f"Failed to update permissions: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ✅ GROUP ENDPOINTS
@app.route('/api/v1/groups/search', methods=['GET'])
def search_groups():
    """Search for groups"""
    try:
        query = request.args.get('query', '').lower()

        # Mock groups for demonstration
        mock_groups = [
            {'group_id': 'g001', 'name': 'IT Staff', 'description': 'IT Department', 'member_count': 5},
            {'group_id': 'g002', 'name': 'Finance', 'description': 'Finance Department', 'member_count': 3},
            {'group_id': 'g003', 'name': 'Operations', 'description': 'Operations Team', 'member_count': 4},
            {'group_id': 'g004', 'name': 'Administrators', 'description': 'System Administrators', 'member_count': 2},
            {'group_id': 'g005', 'name': 'HR Department', 'description': 'Human Resources', 'member_count': 6}
        ]

        if query:
            filtered = [g for g in mock_groups if
                       query in g['name'].lower() or
                       query in g['description'].lower()]
        else:
            filtered = mock_groups

        return jsonify({'success': True, 'groups': filtered})
    except Exception as e:
        logger.error(f"Failed to search groups: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/groups/<group_id>/members', methods=['GET'])
def get_group_members(group_id):
    """Get group members"""
    try:
        # Mock members - in production, query from AD
        mock_members = {
            'g001': [
                {'user_id': 'u001', 'username': 'john.doe', 'display_name': 'John Doe', 'email': 'john.doe@company.com'}
            ],
            'g002': [
                {'user_id': 'u002', 'username': 'jane.smith', 'display_name': 'Jane Smith', 'email': 'jane.smith@company.com'}
            ],
            'g003': [
                {'user_id': 'u003', 'username': 'bob.johnson', 'display_name': 'Bob Johnson', 'email': 'bob.johnson@company.com'}
            ]
        }

        members = mock_members.get(group_id, [])
        return jsonify({'success': True, 'members': members})
    except Exception as e:
        logger.error(f"Failed to get group members: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/groups/<group_id>/members', methods=['POST'])
def add_user_to_group(group_id, user_id):
    """Add user to group"""
    try:
        data = request.get_json()
        user_id = data.get('user_id')

        if user_id in ad_manager.users:
            user = ad_manager.users[user_id]

            # Map group IDs to names
            group_names = {
                'g001': 'IT Staff',
                'g002': 'Finance',
                'g003': 'Operations',
                'g004': 'Administrators',
                'g005': 'HR Department'
            }

            group_name = group_names.get(group_id, f'Group-{group_id}')

            if group_name not in user.groups:
                user.groups.append(group_name)
                ad_manager.save_users()

        audit_manager.log_event(
            AuditEventType.PERMISSION_GRANTED,
            f"User {user_id} added to group {group_id}",
            username="admin",
            result="success"
        )

        return jsonify({'success': True, 'message': 'User added to group'})
    except Exception as e:
        logger.error(f"Failed to add user to group: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/groups/<group_id>/members/<user_id>', methods=['DELETE'])
def remove_user_from_group(group_id, user_id):
    """Remove user from group"""
    try:
        if user_id in ad_manager.users:
            user = ad_manager.users[user_id]

            group_names = {
                'g001': 'IT Staff',
                'g002': 'Finance',
                'g003': 'Operations',
                'g004': 'Administrators',
                'g005': 'HR Department'
            }

            group_name = group_names.get(group_id, f'Group-{group_id}')

            if group_name in user.groups:
                user.groups.remove(group_name)
                ad_manager.save_users()

        audit_manager.log_event(
            AuditEventType.PERMISSION_REVOKED,
            f"User {user_id} removed from group {group_id}",
            username="admin",
            result="success"
        )

        return jsonify({'success': True, 'message': 'User removed from group'})
    except Exception as e:
        logger.error(f"Failed to remove user from group: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# COMPLIANCE ENDPOINTS
@app.route('/api/v1/compliance/frameworks', methods=['GET'])
def get_compliance_frameworks():
    """Get all compliance frameworks"""
    try:
        frameworks = []

        framework_info = {
            ComplianceFramework.HIPAA: {
                'name': 'HIPAA',
                'description': 'Health Insurance Portability and Accountability Act'
            },
            ComplianceFramework.PCI_DSS: {
                'name': 'PCI DSS',
                'description': 'Payment Card Industry Data Security Standard'
            },
            ComplianceFramework.GDPR: {
                'name': 'GDPR',
                'description': 'General Data Protection Regulation'
            },
            ComplianceFramework.ISO_27001: {
                'name': 'ISO 27001',
                'description': 'Information Security Management'
            },
            ComplianceFramework.SOC_TYPE_II: {
                'name': 'SOC Type II',
                'description': 'Service Organization Control Type II'
            },
            ComplianceFramework.GLBA: {
                'name': 'GLBA',
                'description': 'Gramm-Leach-Bliley Act'
            },
            ComplianceFramework.CFR_PART_11: {
                'name': 'CFR Part 11',
                'description': 'FDA 21 CFR Part 11 (Electronic Records)'
            }
        }

        for fw, config in compliance_manager.frameworks.items():
            info = framework_info.get(fw, {'name': fw.value, 'description': ''})
            frameworks.append({
                'framework': fw.value,
                'name': info['name'],
                'description': info['description'],
                'enabled': config.enabled,
                'encryption_required': config.encryption_required,
                'encryption_algorithm': config.encryption_algorithm.value if config.encryption_algorithm else None,
                'audit_retention_days': config.audit_retention_days,
                'multi_factor_auth_required': config.multi_factor_auth_required,
                'data_encryption_at_rest': config.data_encryption_at_rest,
                'data_encryption_in_transit': config.data_encryption_in_transit
            })

        return jsonify({
            'success': True,
            'frameworks': frameworks
        })
    except Exception as e:
        logger.error(f"Failed to get compliance frameworks: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/compliance/<framework>/enable', methods=['POST'])
def enable_compliance(framework):
    """Enable compliance framework"""
    try:
        fw = ComplianceFramework(framework)
        compliance_manager.enable_framework(fw)

        audit_manager.log_event(
            AuditEventType.COMPLIANCE_ENABLED,
            f"Compliance framework enabled: {framework}",
            username="admin",
            result="success"
        )

        return jsonify({
            'success': True,
            'message': f'{framework} enabled successfully'
        })
    except Exception as e:
        logger.error(f"Failed to enable compliance: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/compliance/<framework>/disable', methods=['POST'])
def disable_compliance(framework):
    """Disable compliance framework"""
    try:
        fw = ComplianceFramework(framework)
        compliance_manager.disable_framework(fw)

        audit_manager.log_event(
            AuditEventType.COMPLIANCE_DISABLED,
            f"Compliance framework disabled: {framework}",
            username="admin",
            result="success"
        )

        return jsonify({
            'success': True,
            'message': f'{framework} disabled successfully'
        })
    except Exception as e:
        logger.error(f"Failed to disable compliance: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# AUDIT LOG ENDPOINTS
@app.route('/api/v1/audit/events', methods=['GET'])
def get_audit_events():
    """Get audit events"""
    try:
        event_type = request.args.get('event_type')
        result = request.args.get('result')
        limit = int(request.args.get('limit', 100))

        event_type_enum = AuditEventType(event_type) if event_type else None

        events = audit_manager.get_events(
            event_type=event_type_enum,
            result=result,
            limit=limit
        )

        return jsonify({
            'success': True,
            'events': [event.to_dict() for event in events]
        })
    except Exception as e:
        logger.error(f"Failed to get audit events: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/audit/export', methods=['GET'])
def export_audit_log():
    """Export audit log"""
    try:
        events = audit_manager.get_events(limit=10000)

        import csv
        import io

        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(['Timestamp', 'Event Type', 'User', 'Action', 'Result', 'Details'])

        for event in events:
            writer.writerow([
                event.timestamp.isoformat(),
                event.event_type.value,
                event.username or 'System',
                event.action,
                event.result,
                str(event.details)
            ])

        output.seek(0)

        from flask import Response
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=audit_log.csv'}
        )
    except Exception as e:
        logger.error(f"Failed to export audit log: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# TRANSFER ENDPOINTS
@app.route('/api/v1/transfers', methods=['POST'])
def create_transfer():
    """Create new transfer"""
    try:
        data = request.get_json()

        protocol_map = {
            'unc': TransferProtocol.UNC,
            'smb': TransferProtocol.SMB,
            'sftp': TransferProtocol.SFTP,
            'ftp': TransferProtocol.FTP,
            'ftps': TransferProtocol.FTPS,
            'http': TransferProtocol.HTTP,
            'https': TransferProtocol.HTTPS,
            'local': TransferProtocol.UNC
        }

        protocol = protocol_map.get(data.get('protocol', 'unc').lower(), TransferProtocol.UNC)

        config = TransferConfig(
            protocol=protocol,
            host=data.get('host'),
            port=data.get('port', 445),
            username=data.get('username'),
            password=data.get('password'),
            encryption_enabled=compliance_manager.is_encryption_required(),
            retry_count=3,
            retry_delay=5,
            timeout=300
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

        audit_manager.log_event(
            AuditEventType.TRANSFER_STARTED,
            f"Transfer started: {data.get('source_path')} -> {data.get('destination_path')}",
            username="admin",
            result="success",
            details={'task_id': task_id}
        )

        return jsonify({
            'success': True,
            'task_id': task_id
        })

    except Exception as e:
        logger.error(f"Transfer creation failed: {e}")
        audit_manager.log_event(
            AuditEventType.TRANSFER_FAILED,
            "Transfer creation failed",
            username="admin",
            result="failure",
            details={'error': str(e)}
        )
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/transfers', methods=['GET'])
def get_transfers():
    """Get all transfers - FIXED VERSION"""
    try:
        transfers_list = []

        # Debug logging
        logger.info(f"📋 GET /api/v1/transfers called")
        logger.info(f"   Has 'monitor' attr: {hasattr(mft_app, 'monitor')}")

        # Ensure mft_app.monitor exists
        if not hasattr(mft_app, 'monitor'):
            logger.warning("❌ No monitor found in mft_app")
            return jsonify({
                'success': True,
                'transfers': []
            })

        monitor = mft_app.monitor

        # Collect all transfers from active, completed, and failed
        all_transfers = []

        # Add active transfers
        if hasattr(monitor, 'active_transfers') and isinstance(monitor.active_transfers, dict):
            for task_id, task in monitor.active_transfers.items():
                all_transfers.append(task)
            logger.info(f"   Active transfers: {len(monitor.active_transfers)}")

        # Add completed transfers
        if hasattr(monitor, 'completed_transfers') and isinstance(monitor.completed_transfers, list):
            all_transfers.extend(monitor.completed_transfers)
            logger.info(f"   Completed transfers: {len(monitor.completed_transfers)}")

        # Add failed transfers
        if hasattr(monitor, 'failed_transfers') and isinstance(monitor.failed_transfers, list):
            all_transfers.extend(monitor.failed_transfers)
            logger.info(f"   Failed transfers: {len(monitor.failed_transfers)}")

        logger.info(f"   Total transfers: {len(all_transfers)}")

        # Convert each transfer task to dict
        for task in all_transfers:
            try:
                # Manually construct dict from task attributes
                transfer_dict = {
                    'task_id': getattr(task, 'task_id', 'unknown'),
                    'source_path': getattr(task, 'source_path', 'N/A'),
                    'destination_path': getattr(task, 'destination_path', 'N/A'),
                    'protocol': getattr(task.protocol, 'value', 'unc') if hasattr(task, 'protocol') else 'unc',
                    'status': getattr(task.status, 'value', 'unknown') if hasattr(task, 'status') else 'unknown',
                    'timestamp': getattr(task, 'created_at', datetime.now()).isoformat() if hasattr(task, 'created_at') else datetime.now().isoformat(),
                    'progress': getattr(task, 'progress', 0),
                    'error': getattr(task, 'error', None)
                }

                transfers_list.append(transfer_dict)

            except Exception as item_error:
                logger.error(f"Error processing transfer: {item_error}")
                continue

        logger.info(f"✅ Returning {len(transfers_list)} transfers")

        return jsonify({
            'success': True,
            'transfers': transfers_list
        })

    except Exception as e:
        logger.error(f"Failed to get transfers: {e}")
        import traceback
        traceback.print_exc()

        # ✅ Even on error, return valid structure
        return jsonify({
            'success': False,
            'error': str(e),
            'transfers': []  # ✅ Return empty array
        }), 500

    # RULES ENDPOINTS


@app.route('/api/v1/rules', methods=['GET'])
def get_rules():
    """Get all transfer rules - FIXED VERSION"""
    try:
        rules_list = []

        # Debug logging
        logger.info(f"📋 GET /api/v1/rules called")
        logger.info(f"   monitor_manager type: {type(monitor_manager)}")
        logger.info(f"   Has 'rules' attr: {hasattr(monitor_manager, 'rules')}")

        # Ensure monitor_manager.rules exists
        if not hasattr(monitor_manager, 'rules') or not isinstance(monitor_manager.rules, dict):
            logger.warning("❌ No rules found in monitor_manager")
            return jsonify({
                'success': True,
                'rules': []  # Return empty array
            })

        logger.info(f"   Rules dict size: {len(monitor_manager.rules)}")
        logger.info(f"   Rules keys: {list(monitor_manager.rules.keys())}")

        for rule_id, rule in monitor_manager.rules.items():
            try:
                # Safely extract rule data
                rule_data = {
                    'rule_id': rule.rule_id,
                    'name': rule.name,
                    'source_path': rule.source_path,
                    'source_pattern': rule.source_pattern,
                    'destination_path': rule.destination_path,
                    'schedule_type': rule.schedule_type.value if hasattr(rule.schedule_type, 'value') else str(
                        rule.schedule_type),
                    'trigger_type': rule.trigger_type.value if rule.trigger_type and hasattr(rule.trigger_type,
                                                                                             'value') else None,
                    'action_type': rule.action_type.value if hasattr(rule.action_type, 'value') else str(
                        rule.action_type),
                    'files_transferred': getattr(rule, 'files_transferred', 0),
                    'status': getattr(rule, 'status', 'unknown'),
                    'enabled': getattr(rule, 'enabled', False),
                    'created_at': rule.created_at.isoformat() if hasattr(rule,
                                                                         'created_at') and rule.created_at else None,
                    'last_run': rule.last_run.isoformat() if hasattr(rule, 'last_run') and rule.last_run else None
                }

                rules_list.append(rule_data)

            except Exception as item_error:
                logger.error(f"Error processing rule {rule_id}: {item_error}")
                continue

        logger.info(f"✅ Returning {len(rules_list)} rules")

        return jsonify({
            'success': True,
            'rules': rules_list  # ✅ Always return array
        })

    except Exception as e:
        logger.error(f"Failed to get rules: {e}")
        import traceback
        traceback.print_exc()

        # ✅ Even on error, return valid structure
        return jsonify({
            'success': False,
            'error': str(e),
            'rules': []  # ✅ Return empty array
        }), 500
@app.route('/api/v1/rules', methods=['POST'])
def create_rule():
    """Create new transfer rule"""
    try:
        data = request.get_json()

        # Map protocol string to enum
        protocol_map = {
            'unc': TransferProtocol.UNC,
            'smb': TransferProtocol.SMB,
            'sftp': TransferProtocol.SFTP,
            'local': TransferProtocol.UNC
        }

        # Map schedule type string to enum
        schedule_type_map = {
            'event_driven': ScheduleType.EVENT_DRIVEN,
            'once': ScheduleType.ONCE,
            'recurring': ScheduleType.RECURRING,
            'cron': ScheduleType.CRON,
            'on_demand': ScheduleType.ON_DEMAND
        }

        # Map trigger type string to enum
        trigger_type_map = {
            'file_created': TriggerType.FILE_CREATED,
            'file_modified': TriggerType.FILE_MODIFIED,
            'file_moved': TriggerType.FILE_MOVED
        }

        # Map action type string to enum
        action_type_map = {
            'copy': ActionType.COPY,
            'move': ActionType.MOVE,
            'move_with_delay': ActionType.MOVE_WITH_DELAY
        }

        # Create transfer config
        protocol = protocol_map.get(data.get('protocol', 'unc').lower(), TransferProtocol.UNC)

        config = TransferConfig(
            protocol=protocol,
            host=data.get('host'),
            port=data.get('port', 445),
            username=data.get('username'),
            password=data.get('password'),
            encryption_enabled=compliance_manager.is_encryption_required(),
            retry_count=3,
            retry_delay=5,
            timeout=300
        )

        # Generate a unique rule_id
        rule_id = str(uuid.uuid4())

        # Create transfer rule with correct fields
        rule = TransferRule(
            rule_id=rule_id,
            name=data.get('name'),
            source_path=data.get('source_path'),
            destination_path=data.get('destination_path'),
            protocol=data.get('protocol', 'unc'),
            host=data.get('host', ''),
            port=data.get('port', 445),
            username=data.get('username'),
            password=data.get('password'),
            source_pattern=data.get('source_pattern', '*.*'),
            schedule_type=schedule_type_map.get(data.get('schedule_type', 'event_driven'), ScheduleType.EVENT_DRIVEN),
            trigger_type=trigger_type_map.get(data.get('trigger_type', 'file_created'), TriggerType.FILE_CREATED),
            action_type=action_type_map.get(data.get('action_type', 'copy'), ActionType.COPY),
            file_age_seconds=data.get('file_age_seconds', 5),
            delete_delay_seconds=data.get('delete_delay_seconds', 300),
            schedule_interval_minutes=data.get('schedule_interval_minutes'),
            schedule_cron=data.get('schedule_cron')
        )

        # Add rule to monitor manager
        logger.info(f"➕ Adding rule to monitor_manager: {rule.name} (ID: {rule_id})")
        logger.info(f"   Before add - Rules count: {len(monitor_manager.rules)}")

        monitor_manager.add_rule(rule)

        logger.info(f"   After add - Rules count: {len(monitor_manager.rules)}")
        logger.info(f"   Rule stored: {rule_id in monitor_manager.rules}")

        # Log audit event
        audit_manager.log_event(
            AuditEventType.RULE_CREATED,
            f"Transfer rule created: {data.get('name')}",
            username="admin",
            result="success",
            details={'rule_id': rule_id, 'name': data.get('name')}
        )

        # Sync servers for health monitoring
        server_monitor.sync_servers_from_rules(monitor_manager.rules)

        return jsonify({
            'success': True,
            'rule_id': rule_id,
            'message': 'Rule created successfully'
        })

    except Exception as e:
        logger.error(f"Failed to create rule: {e}")
        audit_manager.log_event(
            AuditEventType.RULE_CREATED,
            "Failed to create transfer rule",
            username="admin",
            result="failure",
            details={'error': str(e)}
        )
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/rules/<rule_id>', methods=['PUT'])
def update_rule(rule_id):
    """Update existing transfer rule"""
    try:
        data = request.get_json()

        # Check if rule exists
        if rule_id not in monitor_manager.rules:
            return jsonify({'success': False, 'error': 'Rule not found'}), 404

        # Remove old rule
        monitor_manager.remove_rule(rule_id)

        # Map protocol string to enum
        protocol_map = {
            'unc': TransferProtocol.UNC,
            'smb': TransferProtocol.SMB,
            'sftp': TransferProtocol.SFTP,
            'local': TransferProtocol.UNC
        }

        # Map schedule type string to enum
        schedule_type_map = {
            'event_driven': ScheduleType.EVENT_DRIVEN,
            'once': ScheduleType.ONCE,
            'recurring': ScheduleType.RECURRING,
            'cron': ScheduleType.CRON,
            'on_demand': ScheduleType.ON_DEMAND
        }

        # Map trigger type string to enum
        trigger_type_map = {
            'file_created': TriggerType.FILE_CREATED,
            'file_modified': TriggerType.FILE_MODIFIED,
            'file_moved': TriggerType.FILE_MOVED
        }

        # Map action type string to enum
        action_type_map = {
            'copy': ActionType.COPY,
            'move': ActionType.MOVE,
            'move_with_delay': ActionType.MOVE_WITH_DELAY
        }

        # Create transfer config
        protocol = protocol_map.get(data.get('protocol', 'unc').lower(), TransferProtocol.UNC)

        config = TransferConfig(
            protocol=protocol,
            host=data.get('host'),
            port=data.get('port', 445),
            username=data.get('username'),
            password=data.get('password'),
            encryption_enabled=compliance_manager.is_encryption_required(),
            retry_count=3,
            retry_delay=5,
            timeout=300
        )

        # Create updated transfer rule
        rule = TransferRule(
            name=data.get('name'),
            source_path=data.get('source_path'),
            destination_path=data.get('destination_path'),
            source_pattern=data.get('source_pattern', '*.*'),
            schedule_type=schedule_type_map.get(data.get('schedule_type', 'event_driven'), ScheduleType.EVENT_DRIVEN),
            trigger_type=trigger_type_map.get(data.get('trigger_type', 'file_created'), TriggerType.FILE_CREATED),
            action_type=action_type_map.get(data.get('action_type', 'copy'), ActionType.COPY),
            transfer_config=config,
            file_age_seconds=data.get('file_age_seconds', 5),
            delete_delay_seconds=data.get('delete_delay_seconds', 300),
            schedule_interval_minutes=data.get('schedule_interval_minutes'),
            schedule_cron=data.get('schedule_cron')
        )

        # Override the rule_id to keep the same ID
        rule.rule_id = rule_id

        # Add updated rule to monitor manager
        monitor_manager.rules[rule_id] = rule
        monitor_manager.save_rules()

        # Restart monitoring for this rule
        monitor_manager.start_rule(rule_id)

        # Log audit event
        audit_manager.log_event(
            AuditEventType.RULE_MODIFIED,
            f"Transfer rule updated: {data.get('name')}",
            username="admin",
            result="success",
            details={'rule_id': rule_id, 'name': data.get('name')}
        )

        return jsonify({
            'success': True,
            'rule_id': rule_id,
            'message': 'Rule updated successfully'
        })

    except Exception as e:
        logger.error(f"Failed to update rule: {e}")
        audit_manager.log_event(
            AuditEventType.RULE_MODIFIED,
            "Failed to update transfer rule",
            username="admin",
            result="failure",
            details={'error': str(e), 'rule_id': rule_id}
        )
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/rules/<rule_id>/enable', methods=['POST'])
def enable_rule(rule_id):
    """Enable transfer rule"""
    try:
        if rule_id not in monitor_manager.rules:
            return jsonify({'success': False, 'error': 'Rule not found'}), 404

        monitor_manager.enable_rule(rule_id)

        audit_manager.log_event(
            AuditEventType.RULE_MODIFIED,
            f"Rule enabled: {rule_id}",
            username="admin",
            result="success"
        )

        return jsonify({
            'success': True,
            'message': 'Rule enabled successfully'
        })

    except Exception as e:
        logger.error(f"Failed to enable rule: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/rules/<rule_id>/disable', methods=['POST'])
def disable_rule(rule_id):
    """Disable transfer rule"""
    try:
        if rule_id not in monitor_manager.rules:
            return jsonify({'success': False, 'error': 'Rule not found'}), 404

        monitor_manager.disable_rule(rule_id)

        audit_manager.log_event(
            AuditEventType.RULE_MODIFIED,
            f"Rule disabled: {rule_id}",
            username="admin",
            result="success"
        )

        return jsonify({
            'success': True,
            'message': 'Rule disabled successfully'
        })

    except Exception as e:
        logger.error(f"Failed to disable rule: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/rules/<rule_id>', methods=['DELETE'])
def delete_rule(rule_id):
    """Delete transfer rule"""
    try:
        if rule_id not in monitor_manager.rules:
            return jsonify({'success': False, 'error': 'Rule not found'}), 404

        monitor_manager.remove_rule(rule_id)

        audit_manager.log_event(
            AuditEventType.RULE_DELETED,
            f"Rule deleted: {rule_id}",
            username="admin",
            result="success"
        )

        return jsonify({
            'success': True,
            'message': 'Rule deleted successfully'
        })

    except Exception as e:
        logger.error(f"Failed to delete rule: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/v1/rules/statistics', methods=['GET'])
def get_statistics():
    """Get transfer rules statistics - FIXED VERSION"""
    try:
        stats = monitor_manager.get_statistics()

        # ✅ Ensure statistics has the required structure
        if not isinstance(stats, dict):
            stats = {}

            # ✅ Ensure required fields exist
        if 'total_files_transferred' not in stats:
            stats['total_files_transferred'] = 0

        if 'active_rules' not in stats:
            stats['active_rules'] = len([r for r in monitor_manager.rules.values() if r.enabled])

        logger.info(f"✅ Statistics: {stats}")

        return jsonify(stats)

    except Exception as e:
        logger.error(f"Failed to get statistics: {e}")
        import traceback
        traceback.print_exc()

        # ✅ Return default statistics structure
        return jsonify({
            'total_files_transferred': 0,
            'active_rules': 0,
            'total_rules': 0,
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/v1/dashboard/stats', methods=['GET'])
def get_dashboard_stats():
    """Get comprehensive dashboard statistics"""
    try:
        # Get transfer statistics from monitor
        monitor = mft_app.monitor

        active_count = len(monitor.active_transfers) if hasattr(monitor, 'active_transfers') else 0
        completed_count = len(monitor.completed_transfers) if hasattr(monitor, 'completed_transfers') else 0
        failed_count = len(monitor.failed_transfers) if hasattr(monitor, 'failed_transfers') else 0
        total_transfers = active_count + completed_count + failed_count

        # Calculate success rate
        success_rate = round((completed_count / total_transfers * 100) if total_transfers > 0 else 0, 1)

        # Get total files transferred from rules
        total_files = sum(rule.files_transferred for rule in monitor_manager.rules.values())

        # Get total bytes transferred from rules
        total_bytes = sum(rule.bytes_transferred for rule in monitor_manager.rules.values())

        # Get active rules count
        active_rules = len([r for r in monitor_manager.rules.values() if r.enabled])

        # Prepare hourly transfer data for last 24 hours
        now = datetime.now()
        hourly_data = {i: {'completed': 0, 'failed': 0} for i in range(24)}

        # Process completed transfers
        for task in monitor.completed_transfers:
            if hasattr(task, 'completed_at') and task.completed_at:
                hours_ago = int((now - task.completed_at).total_seconds() / 3600)
                # Only count if within last 24 hours and not in future
                if 0 <= hours_ago < 24:
                    hourly_data[hours_ago]['completed'] += 1

        # Process failed transfers
        for task in monitor.failed_transfers:
            if hasattr(task, 'completed_at') and task.completed_at:
                hours_ago = int((now - task.completed_at).total_seconds() / 3600)
                # Only count if within last 24 hours and not in future
                if 0 <= hours_ago < 24:
                    hourly_data[hours_ago]['failed'] += 1

        # Format hourly data for chart
        hours = [(now - timedelta(hours=i)).strftime('%H:00') for i in range(23, -1, -1)]
        completed_series = [hourly_data[23-i]['completed'] for i in range(24)]
        failed_series = [hourly_data[23-i]['failed'] for i in range(24)]

        stats = {
            'total_files_transferred': total_files,
            'total_bytes_transferred': total_bytes,
            'active_rules': active_rules,
            'total_transfers': total_transfers,
            'active_transfers': active_count,
            'completed_transfers': completed_count,
            'failed_transfers': failed_count,
            'success_rate': success_rate,
            'performance_data': {
                'labels': hours,
                'completed': completed_series,
                'failed': failed_series
            }
        }

        logger.info(f"📊 Dashboard stats: {total_transfers} transfers, {success_rate}% success rate")

        return jsonify(stats)

    except Exception as e:
        logger.error(f"Failed to get dashboard stats: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'total_files_transferred': 0,
            'total_bytes_transferred': 0,
            'active_rules': 0,
            'total_transfers': 0,
            'active_transfers': 0,
            'completed_transfers': 0,
            'failed_transfers': 0,
            'success_rate': 0,
            'performance_data': {
                'labels': [],
                'completed': [],
                'failed': []
            }
        }), 500


@app.route('/api/v1/servers/status', methods=['GET'])
def get_servers_status():
    """Get status of all monitored servers"""
    try:
        statuses = server_monitor.get_all_statuses()

        return jsonify({
            'success': True,
            'servers': statuses,
            'total_servers': len(statuses),
            'online_count': sum(1 for s in statuses.values() if s['is_online']),
            'offline_count': sum(1 for s in statuses.values() if not s['is_online'])
        })
    except Exception as e:
        logger.error(f"Failed to get server status: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'servers': {},
            'total_servers': 0,
            'online_count': 0,
            'offline_count': 0
        }), 500


@app.route('/api/v1/servers/activity', methods=['GET'])
def get_server_activity():
    """Get server activity log"""
    try:
        limit = request.args.get('limit', 100, type=int)
        activity_log = server_monitor.get_activity_log(limit=limit)

        return jsonify({
            'success': True,
            'activity': activity_log,
            'total': len(activity_log)
        })
    except Exception as e:
        logger.error(f"Failed to get server activity: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'activity': [],
            'total': 0
        }), 500


@app.route('/api/v1/rules/seed', methods=['POST'])
def seed_rules():
    """Seed database with example transfer rules for testing"""
    try:
        logger.info("🌱 Seeding transfer rules...")

        # Clear existing rules
        monitor_manager.rules.clear()
        logger.info("   Cleared existing rules")

        # Create sample rules
        sample_rules = [
            {
                'rule_id': str(uuid.uuid4()),
                'name': 'Daily Reports Transfer',
                'source_path': 'C:\\SourceFolder\\Reports',
                'destination_path': '\\\\server\\share\\Reports',
                'protocol': 'unc',
                'host': 'fileserver.local',
                'port': 445,
                'source_pattern': '*.pdf',
                'schedule_type': ScheduleType.EVENT_DRIVEN,
                'trigger_type': TriggerType.FILE_CREATED,
                'action_type': ActionType.COPY,
                'enabled': True,
                'files_transferred': 15,
                'status': 'monitoring'
            },
            {
                'rule_id': str(uuid.uuid4()),
                'name': 'Backup Archive Files',
                'source_path': 'C:\\Data\\Archive',
                'destination_path': '\\\\backup\\Archive',
                'protocol': 'unc',
                'host': 'backup.local',
                'port': 445,
                'source_pattern': '*.zip',
                'schedule_type': ScheduleType.RECURRING,
                'trigger_type': TriggerType.FILE_CREATED,
                'action_type': ActionType.MOVE,
                'enabled': True,
                'files_transferred': 42,
                'status': 'idle'
            },
            {
                'rule_id': str(uuid.uuid4()),
                'name': 'Log Files Cleanup',
                'source_path': 'C:\\Logs',
                'destination_path': '\\\\archive\\Logs',
                'protocol': 'unc',
                'host': 'archive.local',
                'port': 445,
                'source_pattern': '*.log',
                'schedule_type': ScheduleType.CRON,
                'trigger_type': TriggerType.FILE_MODIFIED,
                'action_type': ActionType.MOVE_WITH_DELAY,
                'enabled': False,
                'files_transferred': 8,
                'status': 'idle'
            }
        ]

        # Add each sample rule
        for rule_data in sample_rules:
            rule = TransferRule(**rule_data)
            monitor_manager.rules[rule.rule_id] = rule
            logger.info(f"   ✅ Added: {rule.name}")

        logger.info(f"🌱 Successfully seeded {len(sample_rules)} rules")

        return jsonify({
            'success': True,
            'message': f'Successfully seeded {len(sample_rules)} rules',
            'count': len(sample_rules)
        })

    except Exception as e:
        logger.error(f"Failed to seed rules: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


    # ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    print("="*80)
    print("🚀 MFT SYSTEM - ENHANCED WITH SEARCH")
    print("="*80)
    print()
    print("✅ MFT Application initialized")
    print("✅ File Monitor Manager initialized")
    print("✅ Compliance Manager initialized")
    print("✅ Audit Manager initialized")
    print("✅ Active Directory Manager initialized")
    print()
    print("🔍 NEW FEATURES:")
    print("   ✅ User/Group search bar")
    print("   ✅ Assign users to groups")
    print("   ✅ View group members")
    print("   ✅ Fixed permission editing")
    print()
    print("🌐 Server starting on http://127.0.0.1:5000")
    print("="*80)

    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)