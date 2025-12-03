#!/bin/bash
#
# MFT Professional System - Linux Installation Script
# Usage: sudo ./install.sh
#

set -e  # Exit on error

echo "========================================"
echo "MFT Professional System - Installer"
echo "========================================"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "❌ Please run as root (use sudo)"
    exit 1
fi

# Detect Python version
PYTHON_CMD=""
for cmd in python3.11 python3.10 python3.9 python3.8 python3; do
    if command -v $cmd &> /dev/null; then
        version=$($cmd --version 2>&1 | awk '{print $2}')
        major=$(echo $version | cut -d. -f1)
        minor=$(echo $version | cut -d. -f2)
        if [ "$major" -eq 3 ] && [ "$minor" -ge 8 ]; then
            PYTHON_CMD=$cmd
            echo "✅ Found Python: $cmd ($version)"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "❌ Python 3.8+ not found. Please install Python 3.8 or higher."
    exit 1
fi

# Detect package manager and install dependencies
echo ""
echo "Installing system dependencies..."

if command -v apt-get &> /dev/null; then
    # Debian/Ubuntu
    echo "Detected: Debian/Ubuntu"
    apt-get update
    apt-get install -y python3-pip python3-venv python3-dev build-essential libldap2-dev libsasl2-dev
elif command -v yum &> /dev/null; then
    # RHEL/CentOS
    echo "Detected: RHEL/CentOS"
    yum install -y python3-pip python3-devel gcc openldap-devel
elif command -v dnf &> /dev/null; then
    # Fedora
    echo "Detected: Fedora"
    dnf install -y python3-pip python3-devel gcc openldap-devel
else
    echo "⚠️ Unknown package manager. Please install: python3-pip, python3-dev, build-essential, libldap2-dev manually"
fi

# Create installation directory
INSTALL_DIR="/opt/mft-system"
echo ""
echo "Creating installation directory: $INSTALL_DIR"
mkdir -p $INSTALL_DIR
cp -r MFT_PRO/* $INSTALL_DIR/

# Create virtual environment
echo ""
echo "Creating Python virtual environment..."
cd $INSTALL_DIR
$PYTHON_CMD -m venv venv

# Activate virtual environment and install dependencies
echo ""
echo "Installing Python dependencies..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Create data directories
echo ""
echo "Creating data directories..."
mkdir -p /var/lib/mft-system/state
mkdir -p /var/lib/mft-system/local_users
mkdir -p /var/lib/mft-system/audit
mkdir -p /var/log/mft-system
mkdir -p /etc/mft-system

# Set permissions
echo ""
echo "Setting permissions..."
chown -R root:root $INSTALL_DIR
chmod -R 755 $INSTALL_DIR
chown -R nobody:nogroup /var/lib/mft-system
chown -R nobody:nogroup /var/log/mft-system
chmod -R 750 /var/lib/mft-system
chmod -R 750 /var/log/mft-system

# Create systemd service file
echo ""
echo "Creating systemd service..."
cat > /etc/systemd/system/mft-system.service << 'EOF'
[Unit]
Description=MFT Professional System
After=network.target

[Service]
Type=simple
User=nobody
Group=nogroup
WorkingDirectory=/opt/mft-system
Environment="PATH=/opt/mft-system/venv/bin"
ExecStart=/opt/mft-system/venv/bin/python /opt/mft-system/mft_system_with_rules_ui.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/mft-system/mft-system.log
StandardError=append:/var/log/mft-system/mft-system-error.log

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
systemctl daemon-reload

# Create configuration file
if [ ! -f /etc/mft-system/config.env ]; then
    echo ""
    echo "Creating default configuration..."
    cat > /etc/mft-system/config.env << 'EOF'
# MFT System Configuration

# Flask Configuration
FLASK_SECRET_KEY=change-this-to-a-random-secret-key
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
FLASK_DEBUG=False

# Data Directories
MFT_STATE_DIR=/var/lib/mft-system/state
MFT_USERS_DIR=/var/lib/mft-system/local_users
MFT_AUDIT_DIR=/var/lib/mft-system/audit
MFT_LOG_DIR=/var/log/mft-system

# Default Admin Credentials (CHANGE THESE!)
MFT_ADMIN_USERNAME=sysadmin
MFT_ADMIN_PASSWORD=Admin@123
EOF
    chmod 600 /etc/mft-system/config.env
fi

echo ""
echo "========================================"
echo "✅ Installation Complete!"
echo "========================================"
echo ""
echo "Next steps:"
echo ""
echo "1. Edit configuration:"
echo "   sudo nano /etc/mft-system/config.env"
echo ""
echo "2. Start the service:"
echo "   sudo systemctl start mft-system"
echo ""
echo "3. Enable auto-start on boot:"
echo "   sudo systemctl enable mft-system"
echo ""
echo "4. Check status:"
echo "   sudo systemctl status mft-system"
echo ""
echo "5. View logs:"
echo "   sudo journalctl -u mft-system -f"
echo "   or"
echo "   sudo tail -f /var/log/mft-system/mft-system.log"
echo ""
echo "6. Access the web interface:"
echo "   http://YOUR_SERVER_IP:5000"
echo ""
echo "Default login:"
echo "   Username: sysadmin"
echo "   Password: Admin@123"
echo "   (CHANGE THIS IMMEDIATELY!)"
echo ""
echo "========================================"
