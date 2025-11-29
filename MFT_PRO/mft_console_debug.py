#!/usr/bin/env python3
"""
MFT Management Console - Enhanced with Debug Console
Includes detailed error messages and API response viewer
"""

import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

# Import the existing API
try:
    from api_server import app as api_app
except ImportError as e:
    print(f"❌ Error importing API: {e}")
    exit(1)

# Enable CORS
api_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Enhanced Dashboard HTML with Debug Console
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
            width: 400px; 
            max-height: 300px; 
            background: #1a1a1a; 
            color: #00ff00; 
            border: 1px solid #333; 
            font-family: monospace; 
            font-size: 12px;
            overflow-y: auto;
            padding: 10px;
            display: none;
            z-index: 9999;
        }
        #debug-console.active { display: block; }
        .debug-entry { padding: 5px 0; border-bottom: 1px solid #333; }
        .debug-error { color: #ff4444; }
        .debug-success { color: #44ff44; }
        .debug-info { color: #4444ff; }
    </style>
</head>
<body class="bg-gray-100">
    <!-- Debug Console Toggle -->
    <button onclick="toggleDebugConsole()" class="fixed bottom-4 right-4 bg-gray-800 text-white px-4 py-2 rounded-full shadow-lg hover:bg-gray-700 z-50">
        <span class="material-symbols-outlined" style="font-size: 20px;">bug_report</span>
    </button>

    <!-- Debug Console -->
    <div id="debug-console">
        <div class="flex justify-between items-center mb-2 border-b border-gray-700 pb-2">
            <span class="font-bold text-white">Debug Console</span>
            <button onclick="clearDebugConsole()" class="text-red-500 hover:text-red-400">Clear</button>
        </div>
        <div id="debug-content"></div>
    </div>

    <!-- Header -->
    <header class="bg-white py-4 px-4 shadow-sm">
        <div class="container mx-auto">
            <h1 class="text-2xl font-bold text-gray-900">🚀 MFT Management Console</h1>
            <p class="text-sm text-gray-500">Enterprise Managed File Transfer System</p>
        </div>
    </header>

    <!-- Navigation -->
    <div class="bg-white border-b sticky top-0 z-10">
        <div class="container mx-auto flex overflow-x-auto">
            <button onclick="showTab('dashboard', this)" class="tab-btn px-4 py-3 text-sm font-medium border-b-2 border-indigo-600 text-indigo-600">Dashboard</button>
            <button onclick="showTab('transfers', this)" class="tab-btn px-4 py-3 text-sm font-medium text-gray-500">New Transfer</button>
            <button onclick="showTab('devices', this)" class="tab-btn px-4 py-3 text-sm font-medium text-gray-500">Devices</button>
            <button onclick="showTab('rules', this)" class="tab-btn px-4 py-3 text-sm font-medium text-gray-500">Rules</button>
            <button onclick="showTab('users', this)" class="tab-btn px-4 py-3 text-sm font-medium text-gray-500">Users</button>
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
    <main id="transfers" class="tab-content container mx-auto p-4">
        <h2 class="text-2xl font-bold mb-6">Create New Transfer</h2>
        <div class="bg-white rounded-lg shadow p-6">
            <form id="transfer-form" class="space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label class="block text-sm font-medium mb-2">Source Path *</label>
                        <input type="text" id="source-path" required class="w-full border rounded px-3 py-2" placeholder="/path/to/source/file.txt">
                        <p class="text-xs text-gray-500 mt-1">Full path to source file or directory</p>
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Destination Path *</label>
                        <input type="text" id="dest-path" required class="w-full border rounded px-3 py-2" placeholder="/path/to/destination/file.txt">
                        <p class="text-xs text-gray-500 mt-1">Full path to destination location</p>
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Protocol *</label>
                        <select id="protocol" required class="w-full border rounded px-3 py-2">
                            <option value="sftp">SFTP (SSH File Transfer)</option>
                            <option value="ftps">FTPS (FTP over SSL)</option>
                            <option value="https">HTTPS</option>
                            <option value="smb">SMB (Windows Share)</option>
                            <option value="unc">UNC (Network Path)</option>
                            <option value="webdav">WebDAV</option>
                            <option value="as2">AS2 (EDI)</option>
                            <option value="tftp">TFTP</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Host *</label>
                        <input type="text" id="host" required class="w-full border rounded px-3 py-2" placeholder="server.example.com or 192.168.1.100">
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
                            <option value="on_demand">On Demand (Manual execution)</option>
                            <option value="once">Once (Run once at specific time)</option>
                            <option value="recurring">Recurring (Repeat at interval)</option>
                            <option value="cron">Cron (Advanced scheduling)</option>
                        </select>
                    </div>
                </div>

                <div id="interval-group" style="display:none;">
                    <label class="block text-sm font-medium mb-2">Interval (minutes)</label>
                    <input type="number" id="schedule-interval" value="60" class="w-full border rounded px-3 py-2" min="1">
                    <p class="text-xs text-gray-500 mt-1">How often to repeat (e.g., 60 = every hour)</p>
                </div>

                <div id="cron-group" style="display:none;">
                    <label class="block text-sm font-medium mb-2">Cron Expression</label>
                    <input type="text" id="cron-expression" class="w-full border rounded px-3 py-2" placeholder="0 2 * * *">
                    <p class="text-xs text-gray-500 mt-1">Examples: "0 2 * * *" (daily at 2 AM), "*/30 * * * *" (every 30 minutes)</p>
                </div>

                <div>
                    <label class="flex items-center">
                        <input type="checkbox" id="encryption" checked class="mr-2">
                        <span class="text-sm">Enable Encryption (Recommended)</span>
                    </label>
                </div>

                <div>
                    <label class="block text-sm font-medium mb-2">Compliance Frameworks (Optional)</label>
                    <div class="grid grid-cols-2 md:grid-cols-4 gap-2">
                        <label class="flex items-center"><input type="checkbox" value="hipaa" class="mr-2 compliance-check"> HIPAA</label>
                        <label class="flex items-center"><input type="checkbox" value="gdpr" class="mr-2 compliance-check"> GDPR</label>
                        <label class="flex items-center"><input type="checkbox" value="pci_dss" class="mr-2 compliance-check"> PCI DSS</label>
                        <label class="flex items-center"><input type="checkbox" value="sox" class="mr-2 compliance-check"> SOX</label>
                    </div>
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

    <!-- Other tabs remain the same... -->
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
                    <tr><td colspan="7" class="px-6 py-8 text-center text-gray-400">No devices configured</td></tr>
                </tbody>
            </table>
        </div>
    </main>

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
                    <tr><td colspan="5" class="px-6 py-8 text-center text-gray-400">No rules configured</td></tr>
                </tbody>
            </table>
        </div>
    </main>

    <main id="users" class="tab-content container mx-auto p-4">
        <h2 class="text-2xl font-bold mb-6">User Management</h2>
        <div class="bg-white rounded-lg shadow p-6">
            <p class="text-gray-600">Domain configuration and user sync features available here.</p>
        </div>
    </main>

    <main id="audit" class="tab-content container mx-auto p-4">
        <h2 class="text-2xl font-bold mb-6">Audit Log</h2>
        <div class="bg-white rounded-lg shadow overflow-x-auto">
            <table class="w-full text-sm">
                <thead class="bg-indigo-600 text-white">
                    <tr>
                        <th class="px-6 py-3 text-left">Timestamp</th>
                        <th class="px-6 py-3 text-left">Action</th>
                        <th class="px-6 py-3 text-left">User</th>
                        <th class="px-6 py-3 text-left">Details</th>
                    </tr>
                </thead>
                <tbody id="audit-body">
                    <tr><td colspan="4" class="px-6 py-8 text-center text-gray-400">No audit logs</td></tr>
                </tbody>
            </table>
        </div>
    </main>

    <!-- Add Device Modal -->
    <div id="device-modal" class="modal">
        <div class="bg-white rounded-lg p-6 w-full max-w-2xl mx-4">
            <h3 class="text-xl font-bold mb-4">Add New Device</h3>
            <form id="device-form" class="space-y-4">
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label class="block text-sm font-medium mb-2">Device Name *</label>
                        <input type="text" id="device-name" required class="w-full border rounded px-3 py-2" placeholder="Production FTP Server">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Hostname/IP *</label>
                        <input type="text" id="device-hostname" required class="w-full border rounded px-3 py-2" placeholder="ftp.example.com">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Port *</label>
                        <input type="number" id="device-port" required value="22" class="w-full border rounded px-3 py-2">
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-2">Protocol *</label>
                        <select id="device-protocol" required class="w-full border rounded px-3 py-2">
                            <option value="sftp">SFTP</option>
                            <option value="ftps">FTPS</option>
                            <option value="ftp">FTP</option>
                            <option value="https">HTTPS</option>
                            <option value="smb">SMB</option>
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
                    <button type="submit" class="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700">Add Device</button>
                    <button type="button" onclick="closeDeviceModal()" class="px-4 py-2 bg-gray-200 rounded hover:bg-gray-300">Cancel</button>
                </div>
            </form>
        </div>
    </div>

    <script>
        const API_BASE = '/api/v1';

        // Debug Console Functions
        function toggleDebugConsole() {
            document.getElementById('debug-console').classList.toggle('active');
        }

        function clearDebugConsole() {
            document.getElementById('debug-content').innerHTML = '';
        }

        function logDebug(message, type = 'info') {
            const debugContent = document.getElementById('debug-content');
            const timestamp = new Date().toLocaleTimeString();
            const entry = document.createElement('div');
            entry.className = `debug-entry debug-${type}`;
            entry.innerHTML = `[${timestamp}] ${message}`;
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
            if (success) {
                logDebug(`Response: ${JSON.stringify(response, null, 2)}`, 'success');
            } else {
                logDebug(`Error Response: ${JSON.stringify(response, null, 2)}`, 'error');
            }
        }

        // Tab switching
        function showTab(tabId, btnElement) {
            document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(btn => {
                btn.classList.remove('text-indigo-600', 'border-b-2', 'border-indigo-600');
                btn.classList.add('text-gray-500');
            });
            document.getElementById(tabId).classList.add('active');
            btnElement.classList.remove('text-gray-500');
            btnElement.classList.add('text-indigo-600', 'border-b-2', 'border-indigo-600');

            if (tabId === 'dashboard') refreshDashboard();
            if (tabId === 'devices') loadDevices();
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
                console.error('Error:', error);
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

            const compliance = Array.from(document.querySelectorAll('.compliance-check:checked')).map(cb => cb.value);
            const scheduleType = document.getElementById('schedule-type').value;

            const data = {
                source_path: document.getElementById('source-path').value,
                destination_path: document.getElementById('dest-path').value,
                protocol: document.getElementById('protocol').value,
                host: document.getElementById('host').value,
                port: parseInt(document.getElementById('port').value),
                username: document.getElementById('username').value || null,
                password: document.getElementById('password').value || null,
                encryption_enabled: document.getElementById('encryption').checked,
                compliance_frameworks: compliance,
                schedule_type: scheduleType,
                schedule_interval: scheduleType === 'recurring' ? parseInt(document.getElementById('schedule-interval').value) : null,
                cron_expression: scheduleType === 'cron' ? document.getElementById('cron-expression').value : null
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
                        if (typeof responseData.detail === 'string') {
                            errorMsg = responseData.detail;
                        } else if (Array.isArray(responseData.detail)) {
                            errorMsg = responseData.detail.map(e => `${e.loc.join('.')}: ${e.msg}`).join('\\n');
                        } else {
                            errorMsg = JSON.stringify(responseData.detail);
                        }
                    }
                    throw new Error(errorMsg);
                }

                alert(`✅ Transfer created successfully!\\n\\nTask ID: ${responseData.task_id}\\n\\nCheck the Debug Console (bug icon) for full details.`);
                document.getElementById('transfer-form').reset();

            } catch (error) {
                logDebug(`Transfer creation failed: ${error.message}`, 'error');
                alert(`❌ Error creating transfer:\\n\\n${error.message}\\n\\nCheck the Debug Console (bug icon) for full API details.`);
            }
        });

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
                    tbody.innerHTML = '<tr><td colspan="7" class="px-6 py-8 text-center text-gray-400">No devices configured. Click "Add Device" to get started.</td></tr>';
                    return;
                }

                tbody.innerHTML = devices.map(d => {
                    const statusClass = d.status === 'online' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800';
                    return `
                        <tr class="border-b hover:bg-gray-50">
                            <td class="px-6 py-4">${d.id}</td>
                            <td class="px-6 py-4 font-medium">${d.name}</td>
                            <td class="px-6 py-4">${d.hostname}</td>
                            <td class="px-6 py-4">${d.port}</td>
                            <td class="px-6 py-4">${(d.protocol || 'N/A').toUpperCase()}</td>
                            <td class="px-6 py-4"><span class="px-2 py-1 rounded text-xs font-semibold ${statusClass}">${d.status}</span></td>
                            <td class="px-6 py-4">
                                <button onclick="deleteDevice(${d.id})" class="text-red-600 hover:underline text-sm">Delete</button>
                            </td>
                        </tr>
                    `;
                }).join('');
            } catch (error) {
                logDebug(`Error loading devices: ${error.message}`, 'error');
                tbody.innerHTML = '<tr><td colspan="7" class="px-6 py-8 text-center text-red-600">Error loading devices</td></tr>';
            }
        }

        function showAddDeviceModal() {
            document.getElementById('device-modal').classList.add('active');
        }

        function closeDeviceModal() {
            document.getElementById('device-modal').classList.remove('active');
            document.getElementById('device-form').reset();
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

                if (!res.ok) throw new Error(result.detail || 'Failed to add device');
                alert('✅ Device added successfully!');
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
                logDebug(`Device ${id} deleted`, 'success');
                alert('✅ Device deleted');
                loadDevices();
            } catch (error) {
                logDebug(`Error deleting device: ${error.message}`, 'error');
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
                    tbody.innerHTML = '<tr><td colspan="5" class="px-6 py-8 text-center text-gray-400">No rules configured</td></tr>';
                    return;
                }

                tbody.innerHTML = rules.map(r => `
                    <tr class="border-b hover:bg-gray-50">
                        <td class="px-6 py-4">${r.id}</td>
                        <td class="px-6 py-4 font-medium">${r.name}</td>
                        <td class="px-6 py-4">${r.source_path || r.source_pattern || 'N/A'}</td>
                        <td class="px-6 py-4">${r.destination_path || r.destination_pattern || 'N/A'}</td>
                        <td class="px-6 py-4">${r.enabled ? '✅ Yes' : '❌ No'}</td>
                    </tr>
                `).join('');
            } catch (error) {
                logDebug(`Error loading rules: ${error.message}`, 'error');
                tbody.innerHTML = '<tr><td colspan="5" class="px-6 py-8 text-center text-red-600">Error loading rules</td></tr>';
            }
        }

        // Auto-refresh
        setInterval(() => {
            if (document.getElementById('dashboard').classList.contains('active')) {
                refreshDashboard();
            }
        }, 30000);

        // Initial load
        refreshDashboard();
        logDebug('MFT Console initialized', 'success');
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
    print("🚀 MFT Management Console - Enhanced with Debug Features")
    print("=" * 70)
    print()
    print("✨ New Features:")
    print("   🐛 Debug Console - Click bug icon to see API calls and responses")
    print("   📊 Detailed error messages")
    print("   🔍 Request/response logging")
    print()
    print("📊 Management Console: http://localhost:8000/dashboard")
    print("📖 API Documentation: http://localhost:8000/docs")
    print()
    print("=" * 70)
    print()

    uvicorn.run(api_app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()