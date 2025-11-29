"""
PyQt6 Desktop GUI for MFT Application
Professional dark-themed interface for managing file transfers
"""

import sys
import asyncio
import json
from datetime import datetime
from typing import Optional, Dict, Any
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QTableWidget, QTableWidgetItem, QPushButton, QLabel,
    QLineEdit, QComboBox, QTextEdit, QGroupBox, QFormLayout, QSpinBox,
    QCheckBox, QFileDialog, QMessageBox, QProgressBar, QHeaderView,
    QSplitter, QStatusBar, QSystemTrayIcon, QMenu
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread, QObject
from PyQt6.QtGui import QIcon, QAction, QFont, QPalette, QColor
import requests

# API Configuration
API_BASE_URL = "http://localhost:8000/api/v1"


class APIWorker(QThread):
    """Worker thread for async API calls - prevents UI freezing"""
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, func, *args, **kwargs):
        super().__init__()
        self.func = func
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            result = self.func(*self.args, **self.kwargs)
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class APIClient:
    """Client for MFT API"""
    
    def __init__(self, base_url: str = API_BASE_URL):
        self.base_url = base_url
    
    def create_transfer(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new transfer"""
        response = requests.post(f"{self.base_url}/transfers", json=data)
        response.raise_for_status()
        return response.json()
    
    def get_transfer_status(self, task_id: str) -> Dict[str, Any]:
        """Get transfer status"""
        response = requests.get(f"{self.base_url}/transfers/{task_id}")
        response.raise_for_status()
        return response.json()
    
    def list_transfers(self, status: Optional[str] = None, limit: int = 100) -> list:
        """List transfers"""
        params = {"limit": limit}
        if status:
            params["status"] = status
        response = requests.get(f"{self.base_url}/transfers", params=params)
        response.raise_for_status()
        return response.json()
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics"""
        response = requests.get(f"{self.base_url}/statistics")
        response.raise_for_status()
        return response.json()
    
    def get_dashboard_data(self, time_range: int = 60) -> Dict[str, Any]:
        """Get monitoring dashboard data"""
        response = requests.get(
            f"{self.base_url}/monitoring/dashboard",
            params={"time_range_minutes": time_range}
        )
        response.raise_for_status()
        return response.json()
    
    def get_alerts(self) -> list:
        """Get active alerts"""
        response = requests.get(f"{self.base_url}/alerts")
        response.raise_for_status()
        return response.json()
    
    def get_supported_protocols(self) -> list:
        """Get supported protocols"""
        response = requests.get(f"{self.base_url}/config/protocols")
        response.raise_for_status()
        return response.json()["protocols"]
    
    def get_compliance_frameworks(self) -> list:
        """Get compliance frameworks"""
        response = requests.get(f"{self.base_url}/config/compliance-frameworks")
        response.raise_for_status()
        return response.json()["frameworks"]

    def request(self, method: str, endpoint: str, data: Dict[str, Any] = None) -> Any:
        """Generic request method for advanced endpoints"""
        url = f"{self.base_url}{endpoint}"
        if method.upper() == "GET":
            response = requests.get(url)
        elif method.upper() == "POST":
            response = requests.post(url, json=data)
        elif method.upper() == "PUT":
            response = requests.put(url, json=data)
        elif method.upper() == "DELETE":
            response = requests.delete(url)
        else:
            raise ValueError(f"Unsupported method: {method}")
        response.raise_for_status()
        if response.content:
            return response.json()
        return {}


class TransferWorker(QThread):
    """Worker thread for monitoring transfers"""
    transfer_updated = pyqtSignal(dict)
    
    def __init__(self, api_client: APIClient, task_id: str):
        super().__init__()
        self.api_client = api_client
        self.task_id = task_id
        self.running = True
    
    def run(self):
        """Monitor transfer until completion"""
        while self.running:
            try:
                status = self.api_client.get_transfer_status(self.task_id)
                self.transfer_updated.emit(status)
                
                if status['status'] in ['completed', 'failed', 'cancelled']:
                    break
                
                self.msleep(1000)  # Check every second
            except Exception as e:
                print(f"Error monitoring transfer: {e}")
                break
    
    def stop(self):
        """Stop monitoring"""
        self.running = False


class NewTransferDialog(QWidget):
    """Dialog for creating new transfers"""
    transfer_created = pyqtSignal(str)

    def __init__(self, api_client: APIClient, parent=None):
        super().__init__(parent)
        self.api_client = api_client
        self._workers = []
        # Make this a separate window, not embedded in parent
        self.setWindowFlags(Qt.WindowType.Window)
        self.setup_ui()
        # Load data asynchronously after UI is set up
        QTimer.singleShot(0, self._load_initial_data)

    def _load_initial_data(self):
        """Load protocols and frameworks asynchronously"""
        def fetch_data():
            try:
                protocols = self.api_client.get_supported_protocols()
            except:
                protocols = None
            try:
                frameworks = self.api_client.get_compliance_frameworks()
            except:
                frameworks = None
            return {'protocols': protocols, 'frameworks': frameworks}

        worker = APIWorker(fetch_data)
        worker.finished.connect(self._on_data_loaded)
        self._workers.append(worker)
        worker.start()

    def _on_data_loaded(self, data):
        """Handle initial data loaded"""
        if data.get('protocols'):
            self.protocol.clear()
            self.protocol.addItems([p.upper() for p in data['protocols']])

        if data.get('frameworks'):
            # Clear existing and add new
            for i in reversed(range(self.compliance_layout.count())):
                widget = self.compliance_layout.itemAt(i).widget()
                if widget:
                    widget.deleteLater()
            self.compliance_checks = {}
            for framework in data['frameworks']:
                check = QCheckBox(framework.upper())
                self.compliance_checks[framework] = check
                self.compliance_layout.addWidget(check)

    def setup_ui(self):
        """Setup UI"""
        self.setWindowTitle("New Transfer")
        self.setMinimumWidth(600)

        layout = QVBoxLayout()

        # Form
        form_layout = QFormLayout()

        # Source path
        source_layout = QHBoxLayout()
        self.source_path = QLineEdit()
        self.source_browse = QPushButton("Browse...")
        self.source_browse.clicked.connect(self.browse_source)
        source_layout.addWidget(self.source_path)
        source_layout.addWidget(self.source_browse)
        form_layout.addRow("Source Path:", source_layout)

        # Destination path
        self.dest_path = QLineEdit()
        form_layout.addRow("Destination Path:", self.dest_path)

        # Protocol - start with defaults, update async
        self.protocol = QComboBox()
        self.protocol.addItems(["SFTP", "HTTPS", "FTPS", "AS2", "SMB", "UNC", "WebDAV", "TFTP"])
        form_layout.addRow("Protocol:", self.protocol)

        # Host
        self.host = QLineEdit()
        form_layout.addRow("Host:", self.host)

        # Port
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(22)
        form_layout.addRow("Port:", self.port)

        # Username
        self.username = QLineEdit()
        form_layout.addRow("Username:", self.username)

        # Password
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form_layout.addRow("Password:", self.password)

        # Private Key
        key_layout = QHBoxLayout()
        self.private_key = QLineEdit()
        self.key_browse = QPushButton("Browse...")
        self.key_browse.clicked.connect(self.browse_key)
        key_layout.addWidget(self.private_key)
        key_layout.addWidget(self.key_browse)
        form_layout.addRow("Private Key:", key_layout)

        # Encryption
        self.encryption = QCheckBox("Enable Encryption")
        self.encryption.setChecked(True)
        form_layout.addRow("", self.encryption)

        # Scheduling
        schedule_group = QGroupBox("Schedule")
        schedule_layout = QFormLayout()

        self.schedule_type = QComboBox()
        self.schedule_type.addItems(["On Demand", "Once", "Recurring", "Cron", "Event Driven"])
        self.schedule_type.currentTextChanged.connect(self._on_schedule_type_changed)
        schedule_layout.addRow("Type:", self.schedule_type)

        # Interval for recurring (in minutes)
        self.schedule_interval = QSpinBox()
        self.schedule_interval.setRange(1, 10080)  # 1 min to 1 week
        self.schedule_interval.setValue(60)
        self.schedule_interval.setSuffix(" min")
        self.schedule_interval_label = QLabel("Interval:")
        schedule_layout.addRow(self.schedule_interval_label, self.schedule_interval)
        self.schedule_interval.setVisible(False)
        self.schedule_interval_label.setVisible(False)

        # Cron expression
        self.cron_expression = QLineEdit()
        self.cron_expression.setPlaceholderText("*/5 * * * * (every 5 minutes)")
        self.cron_label = QLabel("Cron:")
        schedule_layout.addRow(self.cron_label, self.cron_expression)
        self.cron_expression.setVisible(False)
        self.cron_label.setVisible(False)

        schedule_group.setLayout(schedule_layout)
        layout.addLayout(form_layout)
        layout.addWidget(schedule_group)

        # Compliance - start with defaults, update async
        compliance_group = QGroupBox("Compliance Frameworks")
        self.compliance_layout = QVBoxLayout()

        self.compliance_checks = {}
        for framework in ["HIPAA", "GDPR", "PCI DSS"]:
            check = QCheckBox(framework)
            self.compliance_checks[framework.lower().replace(" ", "_")] = check
            self.compliance_layout.addWidget(check)

        compliance_group.setLayout(self.compliance_layout)

        layout.addWidget(compliance_group)

        # Buttons
        button_layout = QHBoxLayout()
        self.create_btn = QPushButton("Create Transfer")
        self.create_btn.clicked.connect(self.create_transfer)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.close)
        button_layout.addWidget(self.create_btn)
        button_layout.addWidget(self.cancel_btn)

        layout.addLayout(button_layout)

        self.setLayout(layout)
    
    def browse_source(self):
        """Browse for source file"""
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Source File")
        if file_path:
            self.source_path.setText(file_path)
    
    def browse_key(self):
        """Browse for private key"""
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Private Key")
        if file_path:
            self.private_key.setText(file_path)

    def _on_schedule_type_changed(self, text):
        """Show/hide schedule fields based on type"""
        is_recurring = text == "Recurring"
        is_cron = text == "Cron"

        self.schedule_interval.setVisible(is_recurring)
        self.schedule_interval_label.setVisible(is_recurring)
        self.cron_expression.setVisible(is_cron)
        self.cron_label.setVisible(is_cron)

    def create_transfer(self):
        """Create the transfer - non-blocking"""
        # Gather data
        compliance_frameworks = [
            name for name, check in self.compliance_checks.items()
            if check.isChecked()
        ]

        # Get schedule type
        schedule_type_map = {
            "On Demand": "on_demand",
            "Once": "once",
            "Recurring": "recurring",
            "Cron": "cron",
            "Event Driven": "event_driven"
        }
        schedule_type = schedule_type_map.get(self.schedule_type.currentText(), "on_demand")

        data = {
            "source_path": self.source_path.text(),
            "destination_path": self.dest_path.text(),
            "protocol": self.protocol.currentText().lower(),
            "host": self.host.text(),
            "port": self.port.value(),
            "username": self.username.text(),
            "password": self.password.text() if self.password.text() else None,
            "private_key_path": self.private_key.text() if self.private_key.text() else None,
            "encryption_enabled": self.encryption.isChecked(),
            "compliance_frameworks": compliance_frameworks,
            "schedule_type": schedule_type,
            "schedule_interval": self.schedule_interval.value() if schedule_type == "recurring" else None,
            "cron_expression": self.cron_expression.text() if schedule_type == "cron" else None
        }

        # Disable button while creating
        self.create_btn.setEnabled(False)
        self.create_btn.setText("Creating...")

        worker = APIWorker(self.api_client.create_transfer, data)
        worker.finished.connect(self._on_transfer_created)
        worker.error.connect(self._on_transfer_error)
        self._workers.append(worker)
        worker.start()

    def _on_transfer_created(self, result):
        """Handle transfer created successfully"""
        QMessageBox.information(self, "Success", f"Transfer created: {result['task_id']}")
        self.transfer_created.emit(result['task_id'])
        self.close()

    def _on_transfer_error(self, error_msg):
        """Handle transfer creation error"""
        self.create_btn.setEnabled(True)
        self.create_btn.setText("Create Transfer")
        QMessageBox.critical(self, "Error", f"Failed to create transfer: {error_msg}")


class TransfersTab(QWidget):
    """Tab for managing transfers"""

    def __init__(self, api_client: APIClient):
        super().__init__()
        self.api_client = api_client
        self._workers = []  # Keep references to prevent garbage collection
        self._refresh_in_progress = False
        self.setup_ui()
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_transfers)
        self.refresh_timer.start(5000)  # Refresh every 5 seconds
    
    def setup_ui(self):
        """Setup UI"""
        layout = QVBoxLayout()
        
        # Toolbar
        toolbar = QHBoxLayout()
        
        self.new_transfer_btn = QPushButton("New Transfer")
        self.new_transfer_btn.clicked.connect(self.show_new_transfer)
        
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_transfers)
        
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "Active", "Completed", "Failed"])
        self.filter_combo.currentTextChanged.connect(self.refresh_transfers)
        
        toolbar.addWidget(self.new_transfer_btn)
        toolbar.addWidget(self.refresh_btn)
        toolbar.addWidget(QLabel("Filter:"))
        toolbar.addWidget(self.filter_combo)
        toolbar.addStretch()
        
        layout.addLayout(toolbar)
        
        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "Task ID", "Protocol", "Status", "Source", "Destination",
            "Created", "Progress", "Actions"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        
        layout.addWidget(self.table)
        
        self.setLayout(layout)
        
        # Initial load
        self.refresh_transfers()
    
    def show_new_transfer(self):
        """Show new transfer dialog"""
        self._transfer_dialog = NewTransferDialog(self.api_client, self)
        self._transfer_dialog.transfer_created.connect(lambda _: self.refresh_transfers())
        self._transfer_dialog.show()
    
    def refresh_transfers(self):
        """Refresh transfers list - non-blocking"""
        if self._refresh_in_progress:
            return  # Skip if already refreshing

        self._refresh_in_progress = True
        filter_text = self.filter_combo.currentText().lower()
        status_filter = None if filter_text == "all" else filter_text

        worker = APIWorker(self.api_client.list_transfers, status=status_filter)
        worker.finished.connect(self._on_transfers_loaded)
        worker.error.connect(self._on_refresh_error)
        self._workers.append(worker)
        worker.start()

    def _on_transfers_loaded(self, transfers):
        """Handle transfers data loaded in background thread"""
        self._refresh_in_progress = False
        try:
            self.table.setRowCount(len(transfers))

            for row, transfer in enumerate(transfers):
                # Task ID
                self.table.setItem(row, 0, QTableWidgetItem(transfer['task_id'][:8] + "..."))

                # Protocol
                self.table.setItem(row, 1, QTableWidgetItem(transfer['protocol'].upper()))

                # Status
                status_item = QTableWidgetItem(transfer['status'].upper())
                if transfer['status'] == 'completed':
                    status_item.setForeground(QColor(0, 200, 0))
                elif transfer['status'] == 'failed':
                    status_item.setForeground(QColor(200, 0, 0))
                elif transfer['status'] in ['in_progress', 'pending']:
                    status_item.setForeground(QColor(255, 165, 0))
                self.table.setItem(row, 2, status_item)

                # Source
                self.table.setItem(row, 3, QTableWidgetItem(transfer['source_path']))

                # Destination
                self.table.setItem(row, 4, QTableWidgetItem(transfer['destination_path']))

                # Created
                created = datetime.fromisoformat(transfer['created_at'].replace('Z', '+00:00'))
                self.table.setItem(row, 5, QTableWidgetItem(created.strftime("%Y-%m-%d %H:%M")))

                # Progress
                if transfer.get('file_size') and transfer.get('transferred_bytes'):
                    progress = int((transfer['transferred_bytes'] / transfer['file_size']) * 100)
                    progress_bar = QProgressBar()
                    progress_bar.setValue(progress)
                    self.table.setCellWidget(row, 6, progress_bar)
                else:
                    self.table.setItem(row, 6, QTableWidgetItem("-"))

                # Actions
                actions_widget = QWidget()
                actions_layout = QHBoxLayout()
                actions_layout.setContentsMargins(2, 2, 2, 2)

                view_btn = QPushButton("View")
                view_btn.clicked.connect(lambda checked, tid=transfer['task_id']: self.view_transfer(tid))
                actions_layout.addWidget(view_btn)

                actions_widget.setLayout(actions_layout)
                self.table.setCellWidget(row, 7, actions_widget)
        except Exception as e:
            print(f"Error updating transfers table: {e}")

    def _on_refresh_error(self, error_msg):
        """Handle refresh error"""
        self._refresh_in_progress = False
        print(f"Error refreshing transfers: {error_msg}")
    
    def view_transfer(self, task_id: str):
        """View transfer details"""
        try:
            transfer = self.api_client.get_transfer_status(task_id)
            details = json.dumps(transfer, indent=2)
            
            msg = QMessageBox(self)
            msg.setWindowTitle("Transfer Details")
            msg.setText(f"Transfer: {task_id[:16]}...")
            msg.setDetailedText(details)
            msg.exec()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to get transfer details: {str(e)}")


class DashboardTab(QWidget):
    """Dashboard tab with statistics and metrics"""

    def __init__(self, api_client: APIClient):
        super().__init__()
        self.api_client = api_client
        self._workers = []  # Keep references to prevent garbage collection
        self._refresh_in_progress = False
        self.setup_ui()

        # Auto-refresh
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_dashboard)
        self.refresh_timer.start(10000)  # Refresh every 10 seconds
    
    def setup_ui(self):
        """Setup UI"""
        layout = QVBoxLayout()
        
        # Statistics Cards
        stats_layout = QHBoxLayout()
        
        # Active Transfers
        self.active_card = self.create_stat_card("Active Transfers", "0")
        stats_layout.addWidget(self.active_card)
        
        # Completed Transfers
        self.completed_card = self.create_stat_card("Completed", "0")
        stats_layout.addWidget(self.completed_card)
        
        # Failed Transfers
        self.failed_card = self.create_stat_card("Failed", "0")
        stats_layout.addWidget(self.failed_card)
        
        # Success Rate
        self.success_card = self.create_stat_card("Success Rate", "0%")
        stats_layout.addWidget(self.success_card)
        
        layout.addLayout(stats_layout)
        
        # System Health
        health_group = QGroupBox("System Health")
        health_layout = QVBoxLayout()
        self.health_label = QLabel("Status: Unknown")
        self.health_label.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        health_layout.addWidget(self.health_label)
        health_group.setLayout(health_layout)
        layout.addWidget(health_group)
        
        # Alerts
        alerts_group = QGroupBox("Active Alerts")
        alerts_layout = QVBoxLayout()
        self.alerts_text = QTextEdit()
        self.alerts_text.setReadOnly(True)
        self.alerts_text.setMaximumHeight(150)
        alerts_layout.addWidget(self.alerts_text)
        alerts_group.setLayout(alerts_layout)
        layout.addWidget(alerts_group)
        
        # Refresh button
        refresh_btn = QPushButton("Refresh Now")
        refresh_btn.clicked.connect(self.refresh_dashboard)
        layout.addWidget(refresh_btn)
        
        layout.addStretch()
        
        self.setLayout(layout)
        
        # Initial load
        self.refresh_dashboard()
    
    def create_stat_card(self, title: str, value: str) -> QGroupBox:
        """Create a statistics card"""
        card = QGroupBox(title)
        layout = QVBoxLayout()
        
        value_label = QLabel(value)
        value_label.setFont(QFont("Arial", 24, QFont.Weight.Bold))
        value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value_label.setObjectName("stat_value")
        
        layout.addWidget(value_label)
        card.setLayout(layout)
        
        return card
    
    def refresh_dashboard(self):
        """Refresh dashboard data - non-blocking"""
        if self._refresh_in_progress:
            return  # Skip if already refreshing

        self._refresh_in_progress = True

        # Fetch all data in parallel using workers
        def fetch_all_data():
            stats = self.api_client.get_statistics()
            dashboard = self.api_client.get_dashboard_data()
            alerts = self.api_client.get_alerts()
            return {'stats': stats, 'dashboard': dashboard, 'alerts': alerts}

        worker = APIWorker(fetch_all_data)
        worker.finished.connect(self._on_dashboard_loaded)
        worker.error.connect(self._on_dashboard_error)
        self._workers.append(worker)
        worker.start()

    def _on_dashboard_loaded(self, data):
        """Handle dashboard data loaded in background thread"""
        self._refresh_in_progress = False
        try:
            stats = data['stats']
            dashboard = data['dashboard']
            alerts = data['alerts']

            # Update statistics
            self.active_card.findChild(QLabel, "stat_value").setText(str(stats['active_transfers']))
            self.completed_card.findChild(QLabel, "stat_value").setText(str(stats['completed_transfers']))
            self.failed_card.findChild(QLabel, "stat_value").setText(str(stats['failed_transfers']))
            self.success_card.findChild(QLabel, "stat_value").setText(f"{stats['success_rate']:.1f}%")

            # Update health
            health = dashboard.get('system_health', {})
            status = health.get('status', 'unknown')
            message = health.get('message', 'Unknown')

            self.health_label.setText(f"Status: {status.upper()} - {message}")

            if status == 'healthy':
                self.health_label.setStyleSheet("color: green;")
            elif status == 'warning':
                self.health_label.setStyleSheet("color: orange;")
            elif status in ['degraded', 'critical']:
                self.health_label.setStyleSheet("color: red;")

            # Update alerts
            if alerts:
                alert_text = "\n".join([
                    f"[{a['severity'].upper()}] {a['title']}: {a['message']}"
                    for a in alerts
                ])
                self.alerts_text.setPlainText(alert_text)
            else:
                self.alerts_text.setPlainText("No active alerts")
        except Exception as e:
            print(f"Error updating dashboard: {e}")

    def _on_dashboard_error(self, error_msg):
        """Handle dashboard refresh error"""
        self._refresh_in_progress = False
        print(f"Error refreshing dashboard: {error_msg}")


class DevicesTab(QWidget):
    """Devices tab for device management and monitoring"""

    def __init__(self, api_client: APIClient):
        super().__init__()
        self.api_client = api_client
        self._workers = []
        self.setup_ui()

    def setup_ui(self):
        """Setup UI"""
        layout = QVBoxLayout()

        # Toolbar
        toolbar = QHBoxLayout()

        add_btn = QPushButton("Add Device")
        add_btn.clicked.connect(self.add_device)
        toolbar.addWidget(add_btn)

        check_btn = QPushButton("Check All")
        check_btn.clicked.connect(self.check_all_devices)
        toolbar.addWidget(check_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_devices)
        toolbar.addWidget(refresh_btn)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Devices table
        self.devices_table = QTableWidget()
        self.devices_table.setColumnCount(7)
        self.devices_table.setHorizontalHeaderLabels([
            "ID", "Name", "Hostname/IP", "Port", "Status", "Last Seen", "Actions"
        ])
        self.devices_table.horizontalHeader().setStretchLastSection(True)
        self.devices_table.setAlternatingRowColors(True)
        layout.addWidget(self.devices_table)

        self.setLayout(layout)
        self.refresh_devices()

    def refresh_devices(self):
        """Refresh devices list"""
        def fetch():
            return self.api_client.request("GET", "/devices")

        worker = APIWorker(fetch)
        worker.finished.connect(self._on_devices_loaded)
        worker.error.connect(lambda e: print(f"Error loading devices: {e}"))
        self._workers.append(worker)
        worker.start()

    def _on_devices_loaded(self, devices):
        """Handle devices loaded"""
        self.devices_table.setRowCount(len(devices))
        for row, device in enumerate(devices):
            self.devices_table.setItem(row, 0, QTableWidgetItem(str(device['id'])))
            self.devices_table.setItem(row, 1, QTableWidgetItem(device['name']))
            self.devices_table.setItem(row, 2, QTableWidgetItem(device['hostname']))
            self.devices_table.setItem(row, 3, QTableWidgetItem(str(device['port'])))

            status_item = QTableWidgetItem(device['status'])
            if device['status'] == 'online':
                status_item.setForeground(Qt.GlobalColor.green)
            else:
                status_item.setForeground(Qt.GlobalColor.red)
            self.devices_table.setItem(row, 4, status_item)

            last_seen = device.get('last_seen', 'Never')
            self.devices_table.setItem(row, 5, QTableWidgetItem(str(last_seen)))

            # Actions
            actions_widget = QWidget()
            actions_layout = QHBoxLayout()
            actions_layout.setContentsMargins(2, 2, 2, 2)

            check_btn = QPushButton("Check")
            check_btn.clicked.connect(lambda checked, d=device: self.check_device(d['id']))
            actions_layout.addWidget(check_btn)

            delete_btn = QPushButton("Delete")
            delete_btn.clicked.connect(lambda checked, d=device: self.delete_device(d['id']))
            actions_layout.addWidget(delete_btn)

            actions_widget.setLayout(actions_layout)
            self.devices_table.setCellWidget(row, 6, actions_widget)

    def add_device(self):
        """Show add device dialog"""
        dialog = DeviceDialog(self)
        if dialog.exec():
            data = dialog.get_data()
            worker = APIWorker(lambda: self.api_client.request("POST", "/devices", data))
            worker.finished.connect(lambda _: self.refresh_devices())
            worker.error.connect(lambda e: QMessageBox.warning(self, "Error", f"Failed to add device: {e}"))
            self._workers.append(worker)
            worker.start()

    def check_device(self, device_id):
        """Check single device status"""
        worker = APIWorker(lambda: self.api_client.request("POST", f"/devices/{device_id}/check"))
        worker.finished.connect(lambda _: self.refresh_devices())
        worker.error.connect(lambda e: print(f"Error checking device: {e}"))
        self._workers.append(worker)
        worker.start()

    def check_all_devices(self):
        """Check all devices"""
        def fetch():
            devices = self.api_client.request("GET", "/devices")
            for d in devices:
                try:
                    self.api_client.request("POST", f"/devices/{d['id']}/check")
                except:
                    pass
            return True

        worker = APIWorker(fetch)
        worker.finished.connect(lambda _: self.refresh_devices())
        self._workers.append(worker)
        worker.start()

    def delete_device(self, device_id):
        """Delete a device"""
        reply = QMessageBox.question(self, "Confirm Delete", "Delete this device?")
        if reply == QMessageBox.StandardButton.Yes:
            worker = APIWorker(lambda: self.api_client.request("DELETE", f"/devices/{device_id}"))
            worker.finished.connect(lambda _: self.refresh_devices())
            worker.error.connect(lambda e: QMessageBox.warning(self, "Error", f"Failed to delete: {e}"))
            self._workers.append(worker)
            worker.start()


class DeviceDialog(QDialog):
    """Dialog for adding/editing devices"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Device")
        self.setMinimumWidth(400)
        self.setup_ui()

    def setup_ui(self):
        layout = QFormLayout()

        self.name_edit = QLineEdit()
        layout.addRow("Name:", self.name_edit)

        self.hostname_edit = QLineEdit()
        layout.addRow("Hostname/IP:", self.hostname_edit)

        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(22)
        layout.addRow("Port:", self.port_spin)

        self.protocol_combo = QComboBox()
        self.protocol_combo.addItems(["sftp", "ftp", "ftps", "scp", "http", "https"])
        layout.addRow("Protocol:", self.protocol_combo)

        self.description_edit = QTextEdit()
        self.description_edit.setMaximumHeight(80)
        layout.addRow("Description:", self.description_edit)

        # Buttons
        buttons = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        layout.addRow(buttons)

        self.setLayout(layout)

    def get_data(self):
        return {
            "name": self.name_edit.text(),
            "hostname": self.hostname_edit.text(),
            "port": self.port_spin.value(),
            "protocol": self.protocol_combo.currentText(),
            "description": self.description_edit.toPlainText()
        }


class RulesTab(QWidget):
    """Transfer rules tab"""

    def __init__(self, api_client: APIClient):
        super().__init__()
        self.api_client = api_client
        self._workers = []
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout()

        # Toolbar
        toolbar = QHBoxLayout()

        add_btn = QPushButton("Add Rule")
        add_btn.clicked.connect(self.add_rule)
        toolbar.addWidget(add_btn)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_rules)
        toolbar.addWidget(refresh_btn)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # Rules table
        self.rules_table = QTableWidget()
        self.rules_table.setColumnCount(8)
        self.rules_table.setHorizontalHeaderLabels([
            "ID", "Name", "Mode", "Source", "Destination", "Device", "Delete After", "Actions"
        ])
        self.rules_table.horizontalHeader().setStretchLastSection(True)
        self.rules_table.setAlternatingRowColors(True)
        layout.addWidget(self.rules_table)

        self.setLayout(layout)
        self.refresh_rules()

    def refresh_rules(self):
        """Refresh rules list"""
        worker = APIWorker(lambda: self.api_client.request("GET", "/rules"))
        worker.finished.connect(self._on_rules_loaded)
        worker.error.connect(lambda e: print(f"Error loading rules: {e}"))
        self._workers.append(worker)
        worker.start()

    def _on_rules_loaded(self, rules):
        """Handle rules loaded"""
        self.rules_table.setRowCount(len(rules))
        for row, rule in enumerate(rules):
            self.rules_table.setItem(row, 0, QTableWidgetItem(str(rule['id'])))
            self.rules_table.setItem(row, 1, QTableWidgetItem(rule['name']))
            self.rules_table.setItem(row, 2, QTableWidgetItem(rule['transfer_mode']))
            self.rules_table.setItem(row, 3, QTableWidgetItem(rule['source_pattern']))
            self.rules_table.setItem(row, 4, QTableWidgetItem(rule['destination_pattern']))
            self.rules_table.setItem(row, 5, QTableWidgetItem(str(rule.get('device_id', 'Any'))))
            self.rules_table.setItem(row, 6, QTableWidgetItem(rule.get('deletion_delay', 'None')))

            # Actions
            actions_widget = QWidget()
            actions_layout = QHBoxLayout()
            actions_layout.setContentsMargins(2, 2, 2, 2)

            delete_btn = QPushButton("Delete")
            delete_btn.clicked.connect(lambda checked, r=rule: self.delete_rule(r['id']))
            actions_layout.addWidget(delete_btn)

            actions_widget.setLayout(actions_layout)
            self.rules_table.setCellWidget(row, 7, actions_widget)

    def add_rule(self):
        """Show add rule dialog"""
        dialog = RuleDialog(self)
        if dialog.exec():
            data = dialog.get_data()
            worker = APIWorker(lambda: self.api_client.request("POST", "/rules", data))
            worker.finished.connect(lambda _: self.refresh_rules())
            worker.error.connect(lambda e: QMessageBox.warning(self, "Error", f"Failed to add rule: {e}"))
            self._workers.append(worker)
            worker.start()

    def delete_rule(self, rule_id):
        """Delete a rule"""
        reply = QMessageBox.question(self, "Confirm Delete", "Delete this rule?")
        if reply == QMessageBox.StandardButton.Yes:
            worker = APIWorker(lambda: self.api_client.request("DELETE", f"/rules/{rule_id}"))
            worker.finished.connect(lambda _: self.refresh_rules())
            self._workers.append(worker)
            worker.start()


class RuleDialog(QDialog):
    """Dialog for adding transfer rules"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Transfer Rule")
        self.setMinimumWidth(500)
        self.setup_ui()

    def setup_ui(self):
        layout = QFormLayout()

        self.name_edit = QLineEdit()
        layout.addRow("Rule Name:", self.name_edit)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["COPY", "MOVE"])
        layout.addRow("Transfer Mode:", self.mode_combo)

        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("/path/to/source/*")
        layout.addRow("Source Pattern:", self.source_edit)

        self.dest_edit = QLineEdit()
        self.dest_edit.setPlaceholderText("/path/to/destination/")
        layout.addRow("Destination:", self.dest_edit)

        self.device_spin = QSpinBox()
        self.device_spin.setRange(0, 9999)
        self.device_spin.setSpecialValueText("Any")
        layout.addRow("Device ID:", self.device_spin)

        self.delay_combo = QComboBox()
        self.delay_combo.addItems(["none", "1_hour", "12_hours", "1_day", "1_week", "1_month", "6_months", "1_year"])
        layout.addRow("Delete After:", self.delay_combo)

        # Buttons
        buttons = QHBoxLayout()
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)
        layout.addRow(buttons)

        self.setLayout(layout)

    def get_data(self):
        data = {
            "name": self.name_edit.text(),
            "transfer_mode": self.mode_combo.currentText(),
            "source_pattern": self.source_edit.text(),
            "destination_pattern": self.dest_edit.text(),
            "deletion_delay": self.delay_combo.currentText()
        }
        if self.device_spin.value() > 0:
            data["device_id"] = self.device_spin.value()
        return data


class UsersTab(QWidget):
    """Users and permissions tab"""

    def __init__(self, api_client: APIClient):
        super().__init__()
        self.api_client = api_client
        self._workers = []
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout()

        # Domain config section
        domain_group = QGroupBox("Domain Configuration")
        domain_layout = QFormLayout()

        self.domain_type = QComboBox()
        self.domain_type.addItems(["active_directory", "ldap"])
        domain_layout.addRow("Type:", self.domain_type)

        self.domain_server = QLineEdit()
        self.domain_server.setPlaceholderText("ldap.example.com")
        domain_layout.addRow("Server:", self.domain_server)

        self.domain_port = QSpinBox()
        self.domain_port.setRange(1, 65535)
        self.domain_port.setValue(389)
        domain_layout.addRow("Port:", self.domain_port)

        self.use_ssl = QCheckBox("Use SSL/TLS (port 636)")
        self.use_ssl.setChecked(True)
        domain_layout.addRow("", self.use_ssl)

        self.domain_base = QLineEdit()
        self.domain_base.setPlaceholderText("DC=example,DC=com")
        domain_layout.addRow("Base DN:", self.domain_base)

        self.bind_user = QLineEdit()
        self.bind_user.setPlaceholderText("CN=service,OU=Users,DC=example,DC=com")
        domain_layout.addRow("Bind User:", self.bind_user)

        self.bind_password = QLineEdit()
        self.bind_password.setEchoMode(QLineEdit.EchoMode.Password)
        domain_layout.addRow("Bind Password:", self.bind_password)

        self.user_search_base = QLineEdit()
        self.user_search_base.setPlaceholderText("OU=Users,DC=example,DC=com")
        domain_layout.addRow("User Search Base:", self.user_search_base)

        # Buttons
        btn_layout = QHBoxLayout()
        test_btn = QPushButton("Test Connection")
        test_btn.clicked.connect(self.test_connection)
        btn_layout.addWidget(test_btn)

        sync_btn = QPushButton("Sync Users")
        sync_btn.clicked.connect(self.sync_users)
        btn_layout.addWidget(sync_btn)

        save_btn = QPushButton("Save Config")
        save_btn.clicked.connect(self.save_domain_config)
        btn_layout.addWidget(save_btn)

        domain_layout.addRow(btn_layout)

        domain_group.setLayout(domain_layout)
        layout.addWidget(domain_group)

        # Users table
        users_group = QGroupBox("Domain Users")
        users_layout = QVBoxLayout()

        self.users_table = QTableWidget()
        self.users_table.setColumnCount(5)
        self.users_table.setHorizontalHeaderLabels(["ID", "Username", "Email", "Groups", "Status"])
        self.users_table.horizontalHeader().setStretchLastSection(True)
        users_layout.addWidget(self.users_table)

        users_group.setLayout(users_layout)
        layout.addWidget(users_group)

        self.setLayout(layout)
        self.refresh_users()

    def refresh_users(self):
        """Refresh users list"""
        worker = APIWorker(lambda: self.api_client.request("GET", "/users"))
        worker.finished.connect(self._on_users_loaded)
        worker.error.connect(lambda e: print(f"Error loading users: {e}"))
        self._workers.append(worker)
        worker.start()

    def _on_users_loaded(self, users):
        """Handle users loaded"""
        self.users_table.setRowCount(len(users))
        for row, user in enumerate(users):
            self.users_table.setItem(row, 0, QTableWidgetItem(str(user['id'])))
            self.users_table.setItem(row, 1, QTableWidgetItem(user['username']))
            self.users_table.setItem(row, 2, QTableWidgetItem(user.get('email', '')))
            self.users_table.setItem(row, 3, QTableWidgetItem(user.get('groups', '')))

            status = "Active" if user.get('is_active', False) else "Inactive"
            self.users_table.setItem(row, 4, QTableWidgetItem(status))

    def sync_users(self):
        """Sync users from domain"""
        worker = APIWorker(lambda: self.api_client.request("POST", "/domain/sync"))
        worker.finished.connect(lambda r: (self.refresh_users(), QMessageBox.information(self, "Sync", f"Synced {r.get('synced', 0)} users")))
        worker.error.connect(lambda e: QMessageBox.warning(self, "Error", f"Sync failed: {e}"))
        self._workers.append(worker)
        worker.start()

    def test_connection(self):
        """Test domain connection"""
        data = self._get_domain_config()
        worker = APIWorker(lambda: self.api_client.request("POST", "/domain/test", data))
        worker.finished.connect(lambda r: QMessageBox.information(self, "Test", r.get('message', 'Connection successful')))
        worker.error.connect(lambda e: QMessageBox.warning(self, "Error", f"Connection failed: {e}"))
        self._workers.append(worker)
        worker.start()

    def save_domain_config(self):
        """Save domain configuration"""
        data = self._get_domain_config()
        worker = APIWorker(lambda: self.api_client.request("POST", "/domain/config", data))
        worker.finished.connect(lambda _: QMessageBox.information(self, "Saved", "Domain configuration saved"))
        worker.error.connect(lambda e: QMessageBox.warning(self, "Error", f"Failed to save: {e}"))
        self._workers.append(worker)
        worker.start()

    def _get_domain_config(self):
        """Get domain config from form fields"""
        return {
            "domain_type": self.domain_type.currentText(),
            "server": self.domain_server.text(),
            "port": self.domain_port.value(),
            "use_ssl": self.use_ssl.isChecked(),
            "base_dn": self.domain_base.text(),
            "bind_user": self.bind_user.text(),
            "bind_password": self.bind_password.text(),
            "user_search_base": self.user_search_base.text()
        }


class AuditTab(QWidget):
    """Audit log viewer tab"""

    def __init__(self, api_client: APIClient):
        super().__init__()
        self.api_client = api_client
        self._workers = []
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout()

        # Filters
        filter_layout = QHBoxLayout()

        filter_layout.addWidget(QLabel("Action:"))
        self.action_filter = QComboBox()
        self.action_filter.addItems(["All", "transfer", "login", "config_change", "user_sync"])
        filter_layout.addWidget(self.action_filter)

        filter_layout.addWidget(QLabel("Limit:"))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(10, 1000)
        self.limit_spin.setValue(100)
        filter_layout.addWidget(self.limit_spin)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_logs)
        filter_layout.addWidget(refresh_btn)

        export_btn = QPushButton("Export")
        export_btn.clicked.connect(self.export_logs)
        filter_layout.addWidget(export_btn)

        filter_layout.addStretch()
        layout.addLayout(filter_layout)

        # Audit log table
        self.audit_table = QTableWidget()
        self.audit_table.setColumnCount(6)
        self.audit_table.setHorizontalHeaderLabels([
            "Timestamp", "Action", "User", "Resource", "Details", "IP Address"
        ])
        self.audit_table.horizontalHeader().setStretchLastSection(True)
        self.audit_table.setAlternatingRowColors(True)
        layout.addWidget(self.audit_table)

        self.setLayout(layout)
        self.refresh_logs()

    def refresh_logs(self):
        """Refresh audit logs"""
        action = self.action_filter.currentText()
        limit = self.limit_spin.value()

        params = f"?limit={limit}"
        if action != "All":
            params += f"&action={action}"

        worker = APIWorker(lambda: self.api_client.request("GET", f"/audit{params}"))
        worker.finished.connect(self._on_logs_loaded)
        worker.error.connect(lambda e: print(f"Error loading logs: {e}"))
        self._workers.append(worker)
        worker.start()

    def _on_logs_loaded(self, logs):
        """Handle logs loaded"""
        self.audit_table.setRowCount(len(logs))
        for row, log in enumerate(logs):
            self.audit_table.setItem(row, 0, QTableWidgetItem(log['timestamp']))
            self.audit_table.setItem(row, 1, QTableWidgetItem(log['action']))
            self.audit_table.setItem(row, 2, QTableWidgetItem(log.get('username', 'System')))
            self.audit_table.setItem(row, 3, QTableWidgetItem(str(log.get('resource_type', ''))))
            details = log.get('details', '')
            if isinstance(details, dict):
                import json
                details = json.dumps(details)
            self.audit_table.setItem(row, 4, QTableWidgetItem(str(details)))
            self.audit_table.setItem(row, 5, QTableWidgetItem(str(log.get('ip_address', ''))))

    def export_logs(self):
        """Export logs to file"""
        from PyQt6.QtWidgets import QFileDialog
        filename, _ = QFileDialog.getSaveFileName(self, "Export Audit Logs", "", "CSV Files (*.csv)")
        if filename:
            # Simple CSV export
            import csv
            with open(filename, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(["Timestamp", "Action", "User", "Resource", "Details", "IP"])
                for row in range(self.audit_table.rowCount()):
                    row_data = []
                    for col in range(6):
                        item = self.audit_table.item(row, col)
                        row_data.append(item.text() if item else "")
                    writer.writerow(row_data)
            QMessageBox.information(self, "Export", f"Exported {self.audit_table.rowCount()} records")


class MFTMainWindow(QMainWindow):
    """Main application window"""
    
    def __init__(self):
        super().__init__()
        self.api_client = APIClient()
        self.setup_ui()
    
    def setup_ui(self):
        """Setup UI"""
        self.setWindowTitle("MFT Application - Managed File Transfer")
        self.setMinimumSize(1200, 700)
        
        # Apply dark theme
        self.apply_dark_theme()
        
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        layout = QVBoxLayout()
        
        # Header
        header = QLabel("Managed File Transfer Application")
        header.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)
        
        # Tabs
        tabs = QTabWidget()
        
        # Dashboard tab
        self.dashboard_tab = DashboardTab(self.api_client)
        tabs.addTab(self.dashboard_tab, "Dashboard")
        
        # Transfers tab
        self.transfers_tab = TransfersTab(self.api_client)
        tabs.addTab(self.transfers_tab, "Transfers")

        # Devices tab
        self.devices_tab = DevicesTab(self.api_client)
        tabs.addTab(self.devices_tab, "Devices")

        # Rules tab
        self.rules_tab = RulesTab(self.api_client)
        tabs.addTab(self.rules_tab, "Rules")

        # Users tab
        self.users_tab = UsersTab(self.api_client)
        tabs.addTab(self.users_tab, "Users")

        # Audit tab
        self.audit_tab = AuditTab(self.api_client)
        tabs.addTab(self.audit_tab, "Audit")

        layout.addWidget(tabs)
        
        central_widget.setLayout(layout)
        
        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Connected to MFT API")
        
        # Menu bar
        self.create_menu_bar()
    
    def create_menu_bar(self):
        """Create menu bar"""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("File")
        
        new_action = QAction("New Transfer", self)
        new_action.triggered.connect(self.transfers_tab.show_new_transfer)
        file_menu.addAction(new_action)
        
        file_menu.addSeparator()
        
        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # View menu
        view_menu = menubar.addMenu("View")
        
        refresh_action = QAction("Refresh All", self)
        refresh_action.triggered.connect(self.refresh_all)
        view_menu.addAction(refresh_action)
        
        # Help menu
        help_menu = menubar.addMenu("Help")
        
        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)
    
    def refresh_all(self):
        """Refresh all data"""
        self.dashboard_tab.refresh_dashboard()
        self.transfers_tab.refresh_transfers()
        self.devices_tab.refresh_devices()
        self.rules_tab.refresh_rules()
        self.users_tab.refresh_users()
        self.audit_tab.refresh_logs()
        self.status_bar.showMessage("Refreshed", 3000)
    
    def show_about(self):
        """Show about dialog"""
        QMessageBox.about(
            self,
            "About MFT Application",
            "Managed File Transfer Application\n\n"
            "Enterprise-grade file transfer solution\n"
            "Supporting multiple protocols and compliance frameworks\n\n"
            "Version 1.0.0"
        )
    
    def apply_dark_theme(self):
        """Apply dark theme to application"""
        dark_stylesheet = """
        QMainWindow {
            background-color: #1e1e1e;
            color: #ffffff;
        }
        QWidget {
            background-color: #1e1e1e;
            color: #ffffff;
        }
        QGroupBox {
            border: 1px solid #3d3d3d;
            border-radius: 5px;
            margin-top: 10px;
            padding-top: 10px;
            font-weight: bold;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px 0 5px;
        }
        QPushButton {
            background-color: #0d47a1;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            font-weight: bold;
        }
        QPushButton:hover {
            background-color: #1565c0;
        }
        QPushButton:pressed {
            background-color: #0a3d91;
        }
        QLineEdit, QTextEdit, QSpinBox, QComboBox {
            background-color: #2d2d2d;
            border: 1px solid #3d3d3d;
            border-radius: 3px;
            padding: 5px;
            color: #ffffff;
        }
        QTableWidget {
            background-color: #2d2d2d;
            alternate-background-color: #252525;
            gridline-color: #3d3d3d;
        }
        QHeaderView::section {
            background-color: #1e1e1e;
            padding: 5px;
            border: 1px solid #3d3d3d;
            font-weight: bold;
        }
        QTabWidget::pane {
            border: 1px solid #3d3d3d;
        }
        QTabBar::tab {
            background-color: #2d2d2d;
            color: #ffffff;
            padding: 8px 16px;
            margin-right: 2px;
        }
        QTabBar::tab:selected {
            background-color: #0d47a1;
        }
        QStatusBar {
            background-color: #2d2d2d;
            color: #ffffff;
        }
        QProgressBar {
            border: 1px solid #3d3d3d;
            border-radius: 3px;
            text-align: center;
        }
        QProgressBar::chunk {
            background-color: #0d47a1;
        }
        """
        self.setStyleSheet(dark_stylesheet)


def main():
    """Main entry point"""
    app = QApplication(sys.argv)
    app.setApplicationName("MFT Application")
    
    window = MFTMainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
