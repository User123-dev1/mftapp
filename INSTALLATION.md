# MFT Professional System - Installation Guide

Complete guide for installing and deploying MFT Professional System on Linux and Windows servers.

---

## Table of Contents

1. [System Requirements](#system-requirements)
2. [Quick Installation](#quick-installation)
3. [Manual Installation](#manual-installation)
4. [Configuration](#configuration)
5. [Service Management](#service-management)
6. [Post-Installation](#post-installation)
7. [Upgrading](#upgrading)
8. [Troubleshooting](#troubleshooting)
9. [Uninstallation](#uninstallation)

---

## System Requirements

### Minimum Requirements

- **CPU**: 2 cores
- **RAM**: 2 GB
- **Disk**: 10 GB free space
- **OS**:
  - Linux: Ubuntu 20.04+, Debian 10+, RHEL 8+, CentOS 8+
  - Windows: Windows Server 2016+, Windows 10+
- **Python**: 3.8 or higher
- **Network**: Port 5000 accessible (or custom port)

### Recommended Requirements

- **CPU**: 4 cores
- **RAM**: 4 GB
- **Disk**: 50 GB free space (for logs and data)
- **OS**: Ubuntu 22.04 LTS or Windows Server 2022
- **Python**: 3.10 or higher

### Network Ports

- **5000/TCP**: Web interface (default, configurable)
- **389/TCP**: LDAP (if using Active Directory)
- **636/TCP**: LDAPS (if using SSL)
- **445/TCP**: SMB/CIFS (for file transfers)
- **22/TCP**: SFTP (for file transfers)

---

## Quick Installation

### Linux (Ubuntu/Debian)

```bash
# 1. Download or clone the repository
git clone https://github.com/yourorg/mftapp.git
cd mftapp

# 2. Run installation script
sudo ./install.sh

# 3. Start the service
sudo systemctl start mft-system
sudo systemctl enable mft-system

# 4. Access web interface
# Open browser: http://YOUR_SERVER_IP:5000
# Login: sysadmin / Admin@123
```

### Windows

```batch
REM 1. Download and extract the package
REM Extract mftapp.zip to C:\

REM 2. Run installation script as Administrator
cd C:\mftapp
install.bat

REM 3. Start the service (if NSSM installed)
net start MFT-System

REM Or double-click the desktop shortcut

REM 4. Access web interface
REM Open browser: http://localhost:5000
REM Login: sysadmin / Admin@123
```

---

## Manual Installation

### Linux Manual Installation

#### 1. Install System Dependencies

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv python3-dev \
    build-essential libldap2-dev libsasl2-dev git
```

**RHEL/CentOS/Fedora:**
```bash
sudo yum install -y python3 python3-pip python3-devel gcc \
    openldap-devel git
```

#### 2. Create Installation Directory

```bash
sudo mkdir -p /opt/mft-system
cd /tmp
git clone https://github.com/yourorg/mftapp.git
sudo cp -r mftapp/MFT_PRO/* /opt/mft-system/
```

#### 3. Create Virtual Environment

```bash
cd /opt/mft-system
sudo python3 -m venv venv
sudo venv/bin/pip install --upgrade pip
sudo venv/bin/pip install -r requirements.txt
```

#### 4. Create Data Directories

```bash
sudo mkdir -p /var/lib/mft-system/{state,local_users,audit}
sudo mkdir -p /var/log/mft-system
sudo mkdir -p /etc/mft-system
```

#### 5. Set Permissions

```bash
sudo chown -R nobody:nogroup /var/lib/mft-system
sudo chown -R nobody:nogroup /var/log/mft-system
sudo chmod -R 750 /var/lib/mft-system
sudo chmod -R 750 /var/log/mft-system
```

#### 6. Create Configuration

```bash
sudo cp config.env.template /etc/mft-system/config.env
sudo nano /etc/mft-system/config.env
# Edit configuration (see Configuration section)
sudo chmod 600 /etc/mft-system/config.env
```

#### 7. Install Systemd Service

```bash
sudo cp mft-system.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable mft-system
sudo systemctl start mft-system
```

### Windows Manual Installation

#### 1. Install Python

1. Download Python 3.10+ from [python.org](https://www.python.org/downloads/)
2. Run installer
3. **Important**: Check "Add Python to PATH"
4. Click "Install Now"

#### 2. Create Installation Directory

```batch
mkdir "C:\Program Files\MFT-System"
REM Extract downloaded package to this directory
xcopy /E /I mftapp\MFT_PRO "C:\Program Files\MFT-System"
```

#### 3. Create Virtual Environment

```batch
cd "C:\Program Files\MFT-System"
python -m venv venv
venv\Scripts\activate.bat
pip install --upgrade pip
pip install -r requirements.txt
```

#### 4. Create Data Directories

```batch
mkdir "C:\ProgramData\MFT-System\state"
mkdir "C:\ProgramData\MFT-System\local_users"
mkdir "C:\ProgramData\MFT-System\audit"
mkdir "C:\ProgramData\MFT-System\logs"
mkdir "C:\ProgramData\MFT-System\config"
```

#### 5. Create Configuration

```batch
copy config.env.template "C:\ProgramData\MFT-System\config\config.env"
notepad "C:\ProgramData\MFT-System\config\config.env"
REM Edit configuration
```

#### 6. Create Startup Script

```batch
echo @echo off > "C:\Program Files\MFT-System\start-mft-system.bat"
echo cd /d "C:\Program Files\MFT-System" >> "C:\Program Files\MFT-System\start-mft-system.bat"
echo call venv\Scripts\activate.bat >> "C:\Program Files\MFT-System\start-mft-system.bat"
echo python mft_system_with_rules_ui.py >> "C:\Program Files\MFT-System\start-mft-system.bat"
echo pause >> "C:\Program Files\MFT-System\start-mft-system.bat"
```

#### 7. Install as Windows Service (Optional)

Using NSSM (Non-Sucking Service Manager):

1. Download NSSM from [nssm.cc](https://nssm.cc/download)
2. Extract to `C:\nssm`
3. Run:

```batch
C:\nssm\nssm.exe install MFT-System "C:\Program Files\MFT-System\venv\Scripts\python.exe" "C:\Program Files\MFT-System\mft_system_with_rules_ui.py"
C:\nssm\nssm.exe set MFT-System AppDirectory "C:\Program Files\MFT-System"
C:\nssm\nssm.exe set MFT-System DisplayName "MFT Professional System"
C:\nssm\nssm.exe set MFT-System Start SERVICE_AUTO_START
net start MFT-System
```

---

## Configuration

### Basic Configuration

Edit the configuration file:
- **Linux**: `/etc/mft-system/config.env`
- **Windows**: `C:\ProgramData\MFT-System\config\config.env`

#### Essential Settings

```bash
# Generate a secure secret key
FLASK_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")

# Change default admin password
MFT_ADMIN_USERNAME=admin
MFT_ADMIN_PASSWORD=YourSecurePassword123!

# Set network binding
FLASK_HOST=0.0.0.0  # Listen on all interfaces
FLASK_PORT=5000      # Change if needed
```

### Active Directory Integration

```bash
AD_SERVER=ldap://dc.company.com
AD_PORT=389
AD_BASE_DN=DC=company,DC=com
AD_BIND_DN=CN=mft-service,CN=Users,DC=company,DC=com
AD_BIND_PASSWORD=service-account-password
AD_USE_SSL=false
AD_SYNC_INTERVAL=60
```

### Security Hardening

```bash
# Disable debug mode
FLASK_DEBUG=False

# Set session timeout
SESSION_TIMEOUT=15  # minutes

# Enforce strong passwords
PASSWORD_MIN_LENGTH=12

# Enable audit logging
AUDIT_ENABLED=true
AUDIT_RETENTION_DAYS=365
```

---

## Service Management

### Linux (systemd)

```bash
# Start service
sudo systemctl start mft-system

# Stop service
sudo systemctl stop mft-system

# Restart service
sudo systemctl restart mft-system

# Check status
sudo systemctl status mft-system

# Enable auto-start on boot
sudo systemctl enable mft-system

# Disable auto-start
sudo systemctl disable mft-system

# View logs
sudo journalctl -u mft-system -f
# or
sudo tail -f /var/log/mft-system/mft-system.log
```

### Windows (Service)

```batch
REM Start service
net start MFT-System

REM Stop service
net stop MFT-System

REM Restart service
net stop MFT-System && net start MFT-System

REM Check status
sc query MFT-System

REM View logs
type "C:\ProgramData\MFT-System\logs\mft-system.log"
```

### Manual Start (No Service)

**Linux:**
```bash
cd /opt/mft-system
source venv/bin/activate
python mft_system_with_rules_ui.py
```

**Windows:**
```batch
"C:\Program Files\MFT-System\start-mft-system.bat"
```

---

## Post-Installation

### 1. First Login

1. Open browser: `http://YOUR_SERVER_IP:5000`
2. Login with default credentials:
   - Username: `sysadmin`
   - Password: `Admin@123`

### 2. Change Admin Password

1. Navigate to Settings → Local Users
2. Edit the sysadmin account
3. Change the password to a strong password
4. Save changes

### 3. Configure Firewall

**Linux (UFW):**
```bash
sudo ufw allow 5000/tcp
sudo ufw reload
```

**Linux (firewalld):**
```bash
sudo firewall-cmd --permanent --add-port=5000/tcp
sudo firewall-cmd --reload
```

**Windows:**
```batch
netsh advfirewall firewall add rule name="MFT System" dir=in action=allow protocol=TCP localport=5000
```

### 4. Configure Active Directory (Optional)

1. Navigate to Settings → Active Directory
2. Enter AD server details
3. Test connection
4. Sync users

### 5. Create Transfer Rules

1. Navigate to Rules tab
2. Click "Create New Rule"
3. Configure source, destination, and schedule
4. Enable the rule

### 6. Setup Compliance (Optional)

1. Navigate to Settings → Compliance
2. Enable required frameworks (GDPR, HIPAA, SOX)
3. Configure compliance settings

---

## Upgrading

### Backup First!

```bash
# Linux
sudo systemctl stop mft-system
sudo tar -czf /tmp/mft-backup-$(date +%Y%m%d).tar.gz \
    /var/lib/mft-system \
    /etc/mft-system

# Windows
net stop MFT-System
# Manually copy C:\ProgramData\MFT-System to backup location
```

### Upgrade Process

**Linux:**
```bash
# Stop service
sudo systemctl stop mft-system

# Backup current installation
sudo mv /opt/mft-system /opt/mft-system.backup

# Install new version
sudo cp -r mftapp-new/MFT_PRO/* /opt/mft-system/

# Reinstall dependencies
cd /opt/mft-system
sudo python3 -m venv venv
sudo venv/bin/pip install -r requirements.txt

# Restore configuration
sudo cp /etc/mft-system/config.env.backup /etc/mft-system/config.env

# Restart service
sudo systemctl start mft-system
```

**Windows:**
```batch
REM Stop service
net stop MFT-System

REM Backup current installation
move "C:\Program Files\MFT-System" "C:\Program Files\MFT-System.backup"

REM Install new version
xcopy /E /I mftapp-new\MFT_PRO "C:\Program Files\MFT-System"

REM Reinstall dependencies
cd "C:\Program Files\MFT-System"
python -m venv venv
venv\Scripts\pip install -r requirements.txt

REM Restore configuration (if needed)

REM Restart service
net start MFT-System
```

---

## Troubleshooting

### Service Won't Start

**Check logs:**
```bash
# Linux
sudo journalctl -u mft-system -n 100
sudo tail -n 100 /var/log/mft-system/mft-system.log

# Windows
type "C:\ProgramData\MFT-System\logs\mft-system.log"
```

**Common issues:**
- Port already in use: Change `FLASK_PORT` in config
- Missing dependencies: Reinstall with `pip install -r requirements.txt`
- Permission errors: Check file ownership and permissions

### Can't Access Web Interface

1. Check service is running
2. Verify firewall rules
3. Check port binding in config
4. Test from server: `curl http://localhost:5000`

### Active Directory Not Connecting

1. Verify AD server is reachable: `ping dc.company.com`
2. Check credentials are correct
3. Verify LDAP port: `telnet dc.company.com 389`
4. Check bind DN format
5. Review logs for LDAP errors

### Transfer Rules Not Working

1. Check rule is enabled
2. Verify source path exists and is accessible
3. Check destination credentials
4. Review Activity Log for errors
5. Verify network connectivity to destination

### High Memory Usage

1. Reduce concurrent transfers: `MAX_CONCURRENT_TRANSFERS=5`
2. Reduce worker threads: `MONITOR_WORKER_THREADS=2`
3. Increase monitoring interval: `MONITOR_POLL_INTERVAL=10`

---

## Uninstallation

### Linux

```bash
# Stop and disable service
sudo systemctl stop mft-system
sudo systemctl disable mft-system

# Remove service file
sudo rm /etc/systemd/system/mft-system.service
sudo systemctl daemon-reload

# Remove installation
sudo rm -rf /opt/mft-system

# Remove data (optional - contains user data!)
sudo rm -rf /var/lib/mft-system
sudo rm -rf /var/log/mft-system
sudo rm -rf /etc/mft-system
```

### Windows

```batch
REM Stop and remove service
net stop MFT-System
sc delete MFT-System

REM Remove installation
rmdir /S /Q "C:\Program Files\MFT-System"

REM Remove data (optional - contains user data!)
rmdir /S /Q "C:\ProgramData\MFT-System"

REM Remove firewall rule
netsh advfirewall firewall delete rule name="MFT System"
```

---

## Support & Documentation

- **Main Documentation**: See `README.md`
- **Launcher Guide**: See `LAUNCHER_GUIDE.md`
- **Issue Tracker**: https://github.com/yourorg/mftapp/issues
- **Email Support**: support@yourcompany.com

---

## Security Notice

**IMPORTANT**: After installation:

1. ✅ Change default admin password immediately
2. ✅ Generate a new `FLASK_SECRET_KEY`
3. ✅ Review and restrict firewall rules
4. ✅ Enable HTTPS if exposing to network
5. ✅ Regularly update the application
6. ✅ Review audit logs periodically
7. ✅ Backup configuration and data regularly

---

## License

MFT Professional System
Copyright © 2024 Your Organization
