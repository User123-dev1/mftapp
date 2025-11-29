#!/usr/bin/env python3
"""
MFT Management Console - Complete Version with All Endpoints
Uses the complete API server with transfers, devices, rules, statistics
"""

import uvicorn
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# Import the COMPLETE API with all endpoints
try:
    from api_server_complete import app as api_app

    print("✅ Loaded complete API server with all endpoints")
except ImportError:
    print("❌ Error: api_server_complete.py not found!")
    print("   Make sure api_server_complete.py is in the same directory")
    exit(1)

# Enable CORS
api_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# [Same DASHBOARD_HTML from previous file - keeping it shorter for file size]
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MFT Management Console</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined" rel="stylesheet"/>
    <style>
        .material-symbols-outlined { font-size: 20px; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
        .modal { display: none; position: fixed; z-index: 50; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.5); }
        .modal.active { display: flex; align-items: center; justify-content: center; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .spinner { animation: spin 1s linear infinite; }
        #debug-console { 
            position: fixed; 
            bottom: 0; 
            right: 0; 
            width: 500px; 
            max-height: 400px; 
            background: #1a1a1a; 
            color: #00ff00; 
            border: 2px solid #00ff00; 
            border-radius: 8px 0 0 0;
            font-family: 'Courier New', monospace; 
            font-size: 11px;
            overflow-y: auto;
            padding: 12px;
            display: none;
            z-index: 9999;
            box-shadow: 0 -4px 20px rgba(0,255,0,0.3);
        }
        #debug-console.active { display: block; }
        .debug-entry { 
            padding: 6px 0; 
            border-bottom: 1px solid #333; 
            word-wrap: break-word;
        }
        .debug-error { color: #ff4444; font-weight: bold; }
        .debug-success { color: #44ff44; }
        .debug-info { color: #4488ff; }
        .debug-warning { color: #ffaa00; }
    </style>
</head>
<body class="bg-gray-100">
    <!-- Debug Console Toggle -->
    <button onclick="toggleDebugConsole()" 
            class="fixed bottom-4 right-4 bg-green-600 text-white px-4 py-2 rounded-full shadow-lg hover:bg-green-700 z-50 flex items-center gap-2"
            title="Open Debug Console">
        <span class="material-symbols-outlined" style="font-size: 24px;">bug_report</span>
        <span class="text-sm font-bold">DEBUG</span>
    </button>

    <!-- Debug Console -->
    <div id="debug-console">
        <div class="flex justify-between items-center mb-3 border-b-2 border-green-500 pb-2">
            <span class="font-bold text-green-400 text-base">🐛 DEBUG CONSOLE</span>
            <div class="flex gap-2">
                <button onclick="clearDebugConsole()" class="text-yellow-400 hover:text-yellow-300 text-xs">CLEAR</button>
                <button onclick="toggleDebugConsole()" class="text-red-400 hover:text-red-300 text-xs">CLOSE</button>
            </div>
        </div>
        <div id="debug-content" class="text-xs"></div>
    </div>

    <!-- Header -->
    <header class="bg-white py-4 px-4 shadow-sm">
        <div class="container mx-auto">
            <h1 class="text-2xl font-bold text-gray-900">🚀 MFT Management Console</h1>
            <p class="text-sm text-gray-500">Enterprise Managed File Transfer System - Complete Edition</p>
        </div>
    </header>

    <!-- Navigation -->
    <div class="bg-white border-b sticky top-0 z-10">
        <div class="container mx-auto flex overflow-x-auto">
            <button onclick="showTab('dashboard', this)" class="tab-btn px-4 py-3 text-sm font-medium border-b-2 border-indigo-600 text-indigo-600">Dashboard</button>
            <button onclick="showTab('new-transfer', this)" class="tab-btn px-4 py-3 text-sm font-medium text-gray-500">New Transfer</button>
            <button onclick="showTab('transfers', this)" class="tab-btn px-4 py-3 text-sm font-medium text-gray-500">Transfers</button>
            <button onclick="showTab('devices', this)" class="tab-btn px-4 py-3 text-sm font-medium text-gray-500">Devices</button>
            <button onclick="showTab('rules', this)" class="tab-btn px-4 py-3 text-sm font-medium text-gray-500">Rules</button>
            <button onclick="showTab('audit', this)" class="tab-btn px-4 py-3 text-sm font-medium text-gray-500">Audit</button>
        </div>
    </div>

    <!-- Dashboard Tab -->
    <main id="dashboard" class="tab-content active container mx-auto p-4">
        <div class="flex justify-between items-center mb-6">
            <h2 class="text-2xl font-bold">System Dashboard</h2>
            <button onclick="refreshDashboard()" class="bg-indigo-600 text-white px-4 py-2 rounded hover:bg-indigo-700">
                <span class="material-symbols-outlined mr-1" style="font-size: 18px;">refresh</span> Refresh
            </button>
        </div>
        <div id="health-status" class="bg-green-50 text-green-700 p-4 rounded-lg mb-6 border border-green-200">
            <p class="font-bold">✅ System Status: Healthy</p>
        </div>
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-4">
            <div class="bg-gradient-to-r from-blue-500 to-blue-600 p-6 rounded-lg text-white">
                <p class="text-sm opacity-90">Active Transfers</p>
                <p id="stat-active" class="text-3xl font-bold mt-1">0</p>
            </div>
            <div class="bg-gradient-to-r from-green-500 to-green-600 p-6 rounded-lg text-white">
                <p class="text-sm opacity-90">Completed</p>
                <p id="stat-completed" class="text-3xl font-bold mt-1">0</p>
            </div>
            <div class="bg-gradient-to-r from-red-500 to-red-600 p-6 rounded-lg text-white">
                <p class="text-sm opacity-90">Failed</p>
                <p id="stat-failed" class="text-3xl font-bold mt-1">0</p>
            </div>
            <div class="bg-gradient-to-r from-purple-500 to-purple-600 p-6 rounded-lg text-white">
                <p class="text-sm opacity-90">Success Rate</p>
                <p id="stat-success" class="text-3xl font-bold mt-1">100%</p>
            </div>
        </div>
    </main>

    <!-- New Transfer Tab -->
    <main id="new-transfer" class="tab-content container mx-auto p-4">
        <h2 class="text-2xl font-bold mb-6">Create New Transfer</h2>

        <!-- Quick Protocol Guide -->
        <div class="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-4">
            <p class="font-semibold text-blue-900 mb-2">📘 Protocol Path Guide:</p>
            <ul class="text-sm text-blue-800 space-y-1">
                <li>• <strong>SFTP:</strong> Use Linux paths like <code>/home/user/file.txt</code></li>
                <li>• <strong>SMB:</strong> Use paths like <code>/C$/Users/username/file.txt</code> (port 445)</li>
                <li>• <strong>UNC:</strong> Use Windows paths like <code>\\\\server\\share\\file.txt</code></li>
                <li>• <strong>FTP/FTPS:</strong> Use paths like <code>/pub/files/file.txt</code></li>
            </ul>
        </div>

        <div class="bg-white rounded-lg shadow p-6">
            <form id="transfer-form" class="space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label class="block text-sm font-medium mb-2">Source Path *</label>
                        <input type="text" id="source-path" required class="w-full border rounded px-3 py-2" placeholder="/path/to/source/file.txt">
                        <p class="text-xs text-gray-500 mt-1">Enter path without protocol prefix</p>
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Destination Path *</label>
                        <input type="text" id="dest-path" required class="w-full border rounded px-3 py-2" placeholder="/path/to/destination/file.txt">
                        <p class="text-xs text-gray-500 mt-1">Enter path without protocol prefix</p>
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Protocol *</label>
                        <select id="protocol" required class="w-full border rounded px-3 py-2" onchange="updateDefaultPort()">
                            <option value="sftp">SFTP (SSH File Transfer) - Port 22</option>
                            <option value="smb">SMB (Windows Share) - Port 445</option>
                            <option value="unc">UNC (Network Path)</option>
                            <option value="ftps">FTPS (FTP over SSL) - Port 990</option>
                            <option value="ftp">FTP - Port 21</option>
                            <option value="https">HTTPS - Port 443</option>
                            <option value="webdav">WebDAV - Port 80</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Host *</label>
                        <input type="text" id="host" required class="w-full border rounded px-3 py-2" placeholder="192.168.1.100 or server.example.com">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Port *</label>
                        <input type="number" id="port" required value="22" class="w-full border rounded px-3 py-2" min="1" max="65535">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Username</label>
                        <input type="text" id="username" class="w-full border rounded px-3 py-2" placeholder="username">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Password</label>
                        <input type="password" id="password" class="w-full border rounded px-3 py-2" placeholder="password">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Schedule Type</label>
                        <select id="schedule-type" onchange="toggleScheduleFields()" class="w-full border rounded px-3 py-2">
                            <option value="on_demand">On Demand (Execute now)</option>
                            <option value="recurring">Recurring (Repeat at interval)</option>
                            <option value="cron">Cron (Advanced scheduling)</option>
                        </select>
                    </div>
                </div>

                <div id="interval-group" style="display:none;">
                    <label class="block text-sm font-medium mb-2">Interval (minutes)</label>
                    <input type="number" id="schedule-interval" value="60" class="w-full border rounded px-3 py-2" min="1">
                </div>

                <div id="cron-group" style="display:none;">
                    <label class="block text-sm font-medium mb-2">Cron Expression</label>
                    <input type="text" id="cron-expression" class="w-full border rounded px-3 py-2" placeholder="0 2 * * *">
                </div>

                <div>
                    <label class="flex items-center">
                        <input type="checkbox" id="encryption" checked class="mr-2">
                        <span class="text-sm">Enable Encryption</span>
                    </label>
                </div>

                <div class="flex gap-3 pt-4">
                    <button type="submit" class="px-6 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700">
                        <span class="material-symbols-outlined mr-1" style="font-size: 18px;">rocket_launch</span> Create Transfer
                    </button>
                    <button type="reset" class="px-6 py-2 bg-gray-200 rounded hover:bg-gray-300">Clear Form</button>
                </div>
            </form>
        </div>
    </main>

    <!-- Transfers History Tab (NEW!) -->
    <main id="transfers" class="tab-content container mx-auto p-4">
        <div class="flex justify-between items-center mb-6">
            <h2 class="text-2xl font-bold">Transfer History</h2>
            <div class="flex gap-2">
                <button onclick="loadTransfers()" class="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700">
                    <span class="material-symbols-outlined mr-1" style="font-size: 18px;">refresh</span> Refresh
                </button>
                <button onclick="clearFailedTransfers()" class="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700">
                    Clear Failed
                </button>
            </div>
        </div>

        <!-- Summary Cards -->
        <div class="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            <div class="bg-blue-50 border border-blue-200 rounded-lg p-4">
                <p class="text-sm text-blue-600 font-medium">Total Transfers</p>
                <p id="transfer-total" class="text-2xl font-bold text-blue-900">0</p>
            </div>
            <div class="bg-green-50 border border-green-200 rounded-lg p-4">
                <p class="text-sm text-green-600 font-medium">Successful</p>
                <p id="transfer-success" class="text-2xl font-bold text-green-900">0</p>
            </div>
            <div class="bg-red-50 border border-red-200 rounded-lg p-4">
                <p class="text-sm text-red-600 font-medium">Failed</p>
                <p id="transfer-failed" class="text-2xl font-bold text-red-900">0</p>
            </div>
            <div class="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
                <p class="text-sm text-yellow-600 font-medium">Pending/Active</p>
                <p id="transfer-pending" class="text-2xl font-bold text-yellow-900">0</p>
            </div>
        </div>

        <!-- Transfers Table -->
        <div class="bg-white rounded-lg shadow overflow-x-auto">
            <table class="w-full text-sm">
                <thead class="bg-indigo-600 text-white">
                    <tr>
                        <th class="px-6 py-3 text-left">Task ID</th>
                        <th class="px-6 py-3 text-left">Status</th>
                        <th class="px-6 py-3 text-left">Protocol</th>
                        <th class="px-6 py-3 text-left">Source</th>
                        <th class="px-6 py-3 text-left">Destination</th>
                        <th class="px-6 py-3 text-left">Created</th>
                        <th class="px-6 py-3 text-left">Actions</th>
                    </tr>
                </thead>
                <tbody id="transfers-body">
                    <tr><td colspan="7" class="px-6 py-8 text-center text-gray-400">Loading...</td></tr>
                </tbody>
            </table>
        </div>
    </main>

    <!-- Devices Tab -->
    <main id="devices" class="tab-content container mx-auto p-4">
        <div class="flex justify-between items-center mb-6">
            <h2 class="text-2xl font-bold">Device Management</h2>
            <div class="flex gap-2">
                <button onclick="showAddDeviceModal()" class="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700">
                    <span class="material-symbols-outlined mr-1" style="font-size: 18px;">add</span> Add Device
                </button>
                <button onclick="loadDevices()" class="px-4 py-2 bg-gray-600 text-white rounded hover:bg-gray-700">Refresh</button>
            </div>
        </div>
        <div class="bg-white rounded-lg shadow overflow-x-auto">
            <table class="w-full text-sm">
                <thead class="bg-indigo-600 text-white">
                    <tr>
                        <th class="px-6 py-3 text-left">ID</th>
                        <th class="px-6 py-3 text-left">Name</th>
                        <th class="px-6 py-3 text-left">Hostname</th>
                        <th class="px-6 py-3 text-left">Port</th>
                        <th class="px-6 py-3 text-left">Protocol</th>
                        <th class="px-6 py-3 text-left">Status</th>
                        <th class="px-6 py-3 text-left">Actions</th>
                    </tr>
                </thead>
                <tbody id="devices-body">
                    <tr><td colspan="7" class="px-6 py-8 text-center text-gray-400">Loading...</td></tr>
                </tbody>
            </table>
        </div>
    </main>

    <!-- Rules Tab -->
    <main id="rules" class="tab-content container mx-auto p-4">
        <div class="flex justify-between items-center mb-6">
            <h2 class="text-2xl font-bold">Transfer Rules</h2>
            <button onclick="loadRules()" class="px-4 py-2 bg-gray-600 text-white rounded hover:bg-gray-700">Refresh</button>
        </div>
        <div class="bg-white rounded-lg shadow overflow-x-auto">
            <table class="w-full text-sm">
                <thead class="bg-indigo-600 text-white">
                    <tr>
                        <th class="px-6 py-3 text-left">ID</th>
                        <th class="px-6 py-3 text-left">Name</th>
                        <th class="px-6 py-3 text-left">Source</th>
                        <th class="px-6 py-3 text-left">Destination</th>
                        <th class="px-6 py-3 text-left">Enabled</th>
                    </tr>
                </thead>
                <tbody id="rules-body">
                    <tr><td colspan="5" class="px-6 py-8 text-center text-gray-400">Loading...</td></tr>
                </tbody>
            </table>
        </div>
    </main>

    <!-- Audit Tab -->
    <main id="audit" class="tab-content container mx-auto p-4">
        <h2 class="text-2xl font-bold mb-6">Audit Log</h2>
        <div class="bg-white rounded-lg shadow overflow-x-auto">
            <table class="w-full text-sm">
                <thead class="bg-indigo-600 text-white">
                    <tr>
                        <th class="px-6 py-3 text-left">Timestamp</th>
                        <th class="px-6 py-3 text-left">Action</th>
                        <th class="px-6 py-3 text-left">Details</th>
                    </tr>
                </thead>
                <tbody id="audit-body">
                    <tr><td colspan="3" class="px-6 py-8 text-center text-gray-400">No audit logs</td></tr>
                </tbody>
            </table>
        </div>
    </main>

    <!-- Device Modal -->
    <div id="device-modal" class="modal">
        <div class="bg-white rounded-lg p-6 w-full max-w-2xl mx-4">
            <h3 class="text-xl font-bold mb-4">Add New Device</h3>
            <form id="device-form" class="space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label class="block text-sm font-medium mb-2">Device Name *</label>
                        <input type="text" id="device-name" required class="w-full border rounded px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Hostname/IP *</label>
                        <input type="text" id="device-hostname" required class="w-full border rounded px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Port *</label>
                        <input type="number" id="device-port" required value="22" class="w-full border rounded px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Protocol *</label>
                        <select id="device-protocol" required class="w-full border rounded px-3 py-2">
                            <option value="sftp">SFTP</option>
                            <option value="smb">SMB</option>
                            <option value="ftps">FTPS</option>
                        </select>
                    </div>
                    <div class="md:col-span-2">
                        <label class="block text-sm font-medium mb-2">Username</label>
                        <input type="text" id="device-username" class="w-full border rounded px-3 py-2">
                    </div>
                    <div class="md:col-span-2">
                        <label class="block text-sm font-medium mb-2">Password</label>
                        <input type="password" id="device-password" class="w-full border rounded px-3 py-2">
                    </div>
                </div>
                <div class="flex gap-3 pt-4">
                    <button type="submit" class="px-4 py-2 bg-indigo-600 text-white rounded">Add Device</button>
                    <button type="button" onclick="closeDeviceModal()" class="px-4 py-2 bg-gray-200 rounded">Cancel</button>
                </div>
            </form>
        </div>
    </div>

    <script>
        const API_BASE = '/api/v1';

        // Debug Console
        function toggleDebugConsole() {
            document.getElementById('debug-console').classList.toggle('active');
        }

        function clearDebugConsole() {
            document.getElementById('debug-content').innerHTML = '';
            logDebug('Console cleared', 'info');
        }

        function logDebug(message, type = 'info') {
            const debugContent = document.getElementById('debug-content');
            const timestamp = new Date().toLocaleTimeString();
            const entry = document.createElement('div');
            entry.className = `debug-entry debug-${type}`;

            let icon = '•';
            if (type === 'error') icon = '✖';
            if (type === 'success') icon = '✓';
            if (type === 'warning') icon = '⚠';

            entry.innerHTML = `<strong>[${timestamp}]</strong> ${icon} ${message}`;
            debugContent.appendChild(entry);
            debugContent.scrollTop = debugContent.scrollHeight;
        }

        function logAPICall(method, url, data = null) {
            logDebug(`API ${method}: ${url}`, 'info');
            if (data) {
                logDebug(`Request: ${JSON.stringify(data, null, 2)}`, 'info');
            }
        }

        function logAPIResponse(response, success = true) {
            const type = success ? 'success' : 'error';
            logDebug(`Response: ${JSON.stringify(response, null, 2)}`, type);
        }

        // Protocol port defaults
        function updateDefaultPort() {
            const protocol = document.getElementById('protocol').value;
            const portInput = document.getElementById('port');
            const ports = {
                'sftp': 22, 'ssh': 22, 'ftp': 21, 'ftps': 990,
                'https': 443, 'http': 80, 'smb': 445, 'webdav': 80
            };
            portInput.value = ports[protocol] || 22;
        }

        // Tab switching
        function showTab(tabId, btnElement) {
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(b => {
                b.classList.remove('text-indigo-600', 'border-b-2', 'border-indigo-600');
                b.classList.add('text-gray-500');
            });
            document.getElementById(tabId).classList.add('active');
            btnElement.classList.remove('text-gray-500');
            btnElement.classList.add('text-indigo-600', 'border-b-2', 'border-indigo-600');

            if (tabId === 'dashboard') refreshDashboard();
            if (tabId === 'devices') loadDevices();
            if (tabId === 'transfers') loadTransfers();
            if (tabId === 'rules') loadRules();
        }

        // Dashboard
        async function refreshDashboard() {
            try {
                logAPICall('GET', `${API_BASE}/statistics`);
                const res = await fetch(`${API_BASE}/statistics`);
                const stats = await res.json();
                logAPIResponse(stats, true);
                document.getElementById('stat-active').textContent = stats.active_transfers || 0;
                document.getElementById('stat-completed').textContent = stats.completed_transfers || 0;
                document.getElementById('stat-failed').textContent = stats.failed_transfers || 0;
                document.getElementById('stat-success').textContent = (stats.success_rate || 100).toFixed(1) + '%';
            } catch (error) {
                logDebug(`Dashboard error: ${error.message}`, 'error');
            }
        }

        // Transfer form
        function toggleScheduleFields() {
            const type = document.getElementById('schedule-type').value;
            document.getElementById('interval-group').style.display = type === 'recurring' ? 'block' : 'none';
            document.getElementById('cron-group').style.display = type === 'cron' ? 'block' : 'none';
        }

        document.getElementById('transfer-form').addEventListener('submit', async (e) => {
            e.preventDefault();

            const data = {
                source_path: document.getElementById('source-path').value,
                destination_path: document.getElementById('dest-path').value,
                protocol: document.getElementById('protocol').value,
                host: document.getElementById('host').value,
                port: parseInt(document.getElementById('port').value),
                username: document.getElementById('username').value || null,
                password: document.getElementById('password').value || null,
                encryption_enabled: document.getElementById('encryption').checked,
                compliance_frameworks: [],
                schedule_type: document.getElementById('schedule-type').value,
                schedule_interval: null,
                cron_expression: null
            };

            try {
                logAPICall('POST', `${API_BASE}/transfers`, data);
                const res = await fetch(`${API_BASE}/transfers`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                });

                const responseData = await res.json();
                logAPIResponse(responseData, res.ok);

                if (!res.ok) {
                    let errorMsg = 'Failed to create transfer';
                    if (responseData.detail) {
                        errorMsg = typeof responseData.detail === 'string' 
                            ? responseData.detail 
                            : JSON.stringify(responseData.detail);
                    }
                    throw new Error(errorMsg);
                }

                alert(`✅ Transfer created successfully!\\n\\nTask ID: ${responseData.task_id}\\nStatus: ${responseData.status}\\n\\nCheck Debug Console for details.`);
                document.getElementById('transfer-form').reset();
                updateDefaultPort();

            } catch (error) {
                logDebug(`Transfer creation failed: ${error.message}`, 'error');
                alert(`❌ Error:\\n\\n${error.message}\\n\\nOpen Debug Console (green button) for full details.`);
            }
        });

        // Load Transfers History
        async function loadTransfers() {
            const tbody = document.getElementById('transfers-body');
            tbody.innerHTML = '<tr><td colspan="7" class="px-6 py-8 text-center"><div class="spinner inline-block w-6 h-6 border-2 border-gray-300 border-t-indigo-600 rounded-full"></div></td></tr>';

            try {
                logAPICall('GET', `${API_BASE}/transfers`);
                const res = await fetch(`${API_BASE}/transfers`);
                const transfers = await res.json();
                logAPIResponse(transfers, true);

                if (!transfers || transfers.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="7" class="px-6 py-8 text-center text-gray-400">No transfers yet. Create your first transfer!</td></tr>';
                    updateTransferStats(0, 0, 0, 0);
                    return;
                }

                // Calculate statistics
                let totalCount = transfers.length;
                let successCount = transfers.filter(t => t.status === 'completed' || t.status === 'success').length;
                let failedCount = transfers.filter(t => t.status === 'failed' || t.status === 'error').length;
                let pendingCount = transfers.filter(t => t.status === 'pending' || t.status === 'active' || t.status === 'scheduled').length;

                updateTransferStats(totalCount, successCount, failedCount, pendingCount);

                // Sort by created date (newest first)
                transfers.sort((a, b) => {
                    const dateA = new Date(a.created_at || 0);
                    const dateB = new Date(b.created_at || 0);
                    return dateB - dateA;
                });

                tbody.innerHTML = transfers.map(t => {
                    let statusClass, statusIcon, statusText;

                    switch(t.status?.toLowerCase()) {
                        case 'completed':
                        case 'success':
                            statusClass = 'bg-green-100 text-green-800';
                            statusIcon = '✓';
                            statusText = 'Completed';
                            break;
                        case 'failed':
                        case 'error':
                            statusClass = 'bg-red-100 text-red-800';
                            statusIcon = '✗';
                            statusText = 'Failed';
                            break;
                        case 'pending':
                            statusClass = 'bg-yellow-100 text-yellow-800';
                            statusIcon = '⏳';
                            statusText = 'Pending';
                            break;
                        case 'active':
                        case 'running':
                            statusClass = 'bg-blue-100 text-blue-800';
                            statusIcon = '▶';
                            statusText = 'Active';
                            break;
                        case 'scheduled':
                            statusClass = 'bg-purple-100 text-purple-800';
                            statusIcon = '📅';
                            statusText = 'Scheduled';
                            break;
                        default:
                            statusClass = 'bg-gray-100 text-gray-800';
                            statusIcon = '?';
                            statusText = t.status || 'Unknown';
                    }

                    const taskIdShort = (t.task_id || 'N/A').substring(0, 8) + '...';
                    const createdDate = t.created_at ? new Date(t.created_at).toLocaleString() : 'N/A';
                    const sourcePath = (t.source_path || 'N/A').substring(0, 40) + (t.source_path?.length > 40 ? '...' : '');
                    const destPath = (t.destination_path || 'N/A').substring(0, 40) + (t.destination_path?.length > 40 ? '...' : '');

                    return `
                        <tr class="border-b hover:bg-gray-50">
                            <td class="px-6 py-4 font-mono text-xs" title="${t.task_id}">${taskIdShort}</td>
                            <td class="px-6 py-4">
                                <span class="px-3 py-1 rounded-full text-xs font-semibold ${statusClass}">
                                    ${statusIcon} ${statusText}
                                </span>
                            </td>
                            <td class="px-6 py-4">
                                <span class="px-2 py-1 bg-indigo-100 text-indigo-800 rounded text-xs font-medium">
                                    ${(t.protocol || 'N/A').toUpperCase()}
                                </span>
                            </td>
                            <td class="px-6 py-4 text-xs" title="${t.source_path}">${sourcePath}</td>
                            <td class="px-6 py-4 text-xs" title="${t.destination_path}">${destPath}</td>
                            <td class="px-6 py-4 text-xs text-gray-600">${createdDate}</td>
                            <td class="px-6 py-4">
                                <button onclick="viewTransferDetails('${t.task_id}')" class="text-indigo-600 hover:underline text-xs mr-2">
                                    Details
                                </button>
                                <button onclick="deleteTransfer('${t.task_id}')" class="text-red-600 hover:underline text-xs">
                                    Delete
                                </button>
                            </td>
                        </tr>
                    `;
                }).join('');

            } catch (error) {
                logDebug(`Error loading transfers: ${error.message}`, 'error');
                tbody.innerHTML = '<tr><td colspan="7" class="px-6 py-8 text-center text-red-600">Error loading transfers. Check debug console.</td></tr>';
                updateTransferStats(0, 0, 0, 0);
            }
        }

        function updateTransferStats(total, success, failed, pending) {
            document.getElementById('transfer-total').textContent = total;
            document.getElementById('transfer-success').textContent = success;
            document.getElementById('transfer-failed').textContent = failed;
            document.getElementById('transfer-pending').textContent = pending;
        }

        async function viewTransferDetails(taskId) {
            try {
                logAPICall('GET', `${API_BASE}/transfers/${taskId}`);
                const res = await fetch(`${API_BASE}/transfers/${taskId}`);
                const transfer = await res.json();
                logAPIResponse(transfer, res.ok);

                if (res.ok) {
                    const details = JSON.stringify(transfer, null, 2);
                    alert(`Transfer Details:\n\n${details}`);
                    logDebug(`Transfer details retrieved for ${taskId}`, 'success');
                } else {
                    alert('Transfer not found');
                }
            } catch (error) {
                logDebug(`Error getting transfer details: ${error.message}`, 'error');
                alert('Error: ' + error.message);
            }
        }

        async function deleteTransfer(taskId) {
            if (!confirm('Delete this transfer record?')) return;
            try {
                logAPICall('DELETE', `${API_BASE}/transfers/${taskId}`);
                const res = await fetch(`${API_BASE}/transfers/${taskId}`, {method: 'DELETE'});

                if (res.ok) {
                    alert('✅ Transfer deleted');
                    loadTransfers();
                } else {
                    alert('Note: Delete endpoint not implemented yet');
                }
            } catch (error) {
                logDebug(`Error deleting transfer: ${error.message}`, 'warning');
                alert('Note: Delete endpoint not implemented yet');
            }
        }

        async function clearFailedTransfers() {
            if (!confirm('Clear all failed transfer records? This cannot be undone.')) return;

            try {
                const res = await fetch(`${API_BASE}/transfers`);
                const transfers = await res.json();

                const failedTransfers = transfers.filter(t => 
                    t.status === 'failed' || t.status === 'error'
                );

                if (failedTransfers.length === 0) {
                    alert('No failed transfers to clear');
                    return;
                }

                let deleted = 0;
                for (const transfer of failedTransfers) {
                    try {
                        await fetch(`${API_BASE}/transfers/${transfer.task_id}`, {method: 'DELETE'});
                        deleted++;
                    } catch (e) { }
                }

                alert(`Attempted to clear ${failedTransfers.length} failed transfers`);
                loadTransfers();

            } catch (error) {
                logDebug(`Error clearing failed transfers: ${error.message}`, 'error');
                alert('Error: ' + error.message);
            }
        }

        // Devices
        async function loadDevices() {
            const tbody = document.getElementById('devices-body');
            tbody.innerHTML = '<tr><td colspan="7" class="px-6 py-8 text-center"><div class="spinner inline-block w-6 h-6 border-2 border-gray-300 border-t-indigo-600 rounded-full"></div></td></tr>';
            try {
                logAPICall('GET', `${API_BASE}/devices`);
                const res = await fetch(`${API_BASE}/devices`);
                const devices = await res.json();
                logAPIResponse(devices, true);

                if (!devices || devices.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="7" class="px-6 py-8 text-center text-gray-400">No devices</td></tr>';
                    return;
                }

                tbody.innerHTML = devices.map(d => {
                    const statusClass = d.status === 'online' ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600';
                    return `
                        <tr class="border-b hover:bg-gray-50">
                            <td class="px-6 py-4">${d.id}</td>
                            <td class="px-6 py-4 font-medium">${d.name}</td>
                            <td class="px-6 py-4">${d.hostname}</td>
                            <td class="px-6 py-4">${d.port}</td>
                            <td class="px-6 py-4">${(d.protocol || 'N/A').toUpperCase()}</td>
                            <td class="px-6 py-4"><span class="px-2 py-1 rounded text-xs ${statusClass}">${d.status}</span></td>
                            <td class="px-6 py-4"><button onclick="deleteDevice(${d.id})" class="text-red-600 hover:underline">Delete</button></td>
                        </tr>
                    `;
                }).join('');
            } catch (error) {
                logDebug(`Error loading devices: ${error.message}`, 'error');
                tbody.innerHTML = '<tr><td colspan="7" class="px-6 py-8 text-center text-red-600">Error loading</td></tr>';
            }
        }

        function showAddDeviceModal() {
            document.getElementById('device-modal').classList.add('active');
        }

        function closeDeviceModal() {
            document.getElementById('device-modal').classList.remove('active');
        }

        document.getElementById('device-form').addEventListener('submit', async (e) => {
            e.preventDefault();
            const data = {
                name: document.getElementById('device-name').value,
                hostname: document.getElementById('device-hostname').value,
                port: parseInt(document.getElementById('device-port').value),
                protocol: document.getElementById('device-protocol').value,
                username: document.getElementById('device-username').value || null,
                password: document.getElementById('device-password').value || null
            };
            try {
                logAPICall('POST', `${API_BASE}/devices`, data);
                const res = await fetch(`${API_BASE}/devices`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                });
                const result = await res.json();
                logAPIResponse(result, res.ok);

                if (!res.ok) throw new Error(result.detail || 'Failed');
                alert('✅ Device added!');
                closeDeviceModal();
                loadDevices();
            } catch (error) {
                logDebug(`Error adding device: ${error.message}`, 'error');
                alert('❌ Error: ' + error.message);
            }
        });

        async function deleteDevice(id) {
            if (!confirm('Delete this device?')) return;
            try {
                logAPICall('DELETE', `${API_BASE}/devices/${id}`);
                await fetch(`${API_BASE}/devices/${id}`, {method: 'DELETE'});
                alert('✅ Device deleted');
                loadDevices();
            } catch (error) {
                logDebug(`Error: ${error.message}`, 'error');
                alert('❌ Error: ' + error.message);
            }
        }

        // Rules
        async function loadRules() {
            const tbody = document.getElementById('rules-body');
            tbody.innerHTML = '<tr><td colspan="5" class="px-6 py-8 text-center"><div class="spinner inline-block w-6 h-6 border-2 border-gray-300 border-t-indigo-600 rounded-full"></div></td></tr>';
            try {
                logAPICall('GET', `${API_BASE}/rules`);
                const res = await fetch(`${API_BASE}/rules`);
                const rules = await res.json();
                logAPIResponse(rules, true);

                if (!rules || rules.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="5" class="px-6 py-8 text-center text-gray-400">No rules</td></tr>';
                    return;
                }

                tbody.innerHTML = rules.map(r => `
                    <tr class="border-b hover:bg-gray-50">
                        <td class="px-6 py-4">${r.id}</td>
                        <td class="px-6 py-4 font-medium">${r.name}</td>
                        <td class="px-6 py-4">${r.source_path || 'N/A'}</td>
                        <td class="px-6 py-4">${r.destination_path || 'N/A'}</td>
                        <td class="px-6 py-4">${r.enabled ? '✅' : '❌'}</td>
                    </tr>
                `).join('');
            } catch (error) {
                logDebug(`Error loading rules: ${error.message}`, 'error');
                tbody.innerHTML = '<tr><td colspan="5" class="px-6 py-8 text-center text-red-600">Error</td></tr>';
            }
        }

        // Initialize
        refreshDashboard();
        logDebug('✅ MFT Console initialized with complete API', 'success');
        logDebug('📡 Transfers endpoint is now available!', 'success');
    </script>
</body>
</html>
"""


# Add routes
@api_app.get("/", response_class=HTMLResponse)
async def root():
    return '<meta http-equiv="refresh" content="0; url=/dashboard" />'


@api_app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


def main():
    print("=" * 70)
    print("🚀 MFT Management Console - COMPLETE EDITION")
    print("=" * 70)
    print()
    print("✅ All Endpoints Active:")
    print("   📦 /api/v1/devices - Device management")
    print("   🚀 /api/v1/transfers - Transfer creation (NOW WORKING!)")
    print("   📋 /api/v1/rules - Transfer rules")
    print("   📊 /api/v1/statistics - System statistics")
    print("   📜 /api/v1/audit - Audit logging")
    print()
    print("🐛 Debug Console: Click green 'DEBUG' button in web UI")
    print("📊 Dashboard: http://localhost:8000/dashboard")
    print("📖 API Docs: http://localhost:8000/docs")
    print()
    print("=" * 70)
    print()

    uvicorn.run(api_app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()