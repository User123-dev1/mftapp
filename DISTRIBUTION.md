# MFT Professional System - Distribution Guide

Guide for packaging and distributing the MFT application to other servers.

---

## Quick Distribution

### Method 1: Direct Package (Recommended for most cases)

Create a distributable archive:

```bash
# From the mftapp directory
cd /home/user/mftapp

# Create distribution archive
tar -czf mft-system-v1.0.0.tar.gz \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='venv' \
    --exclude='*.log' \
    MFT_PRO/ \
    setup.py \
    install.sh \
    install.bat \
    mft-system.service \
    config.env.template \
    INSTALLATION.md \
    LAUNCHER_GUIDE.md \
    README.md

# For Windows, also create a ZIP file
zip -r mft-system-v1.0.0.zip \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='venv' \
    --exclude='*.log' \
    MFT_PRO/ \
    setup.py \
    install.sh \
    install.bat \
    mft-system.service \
    config.env.template \
    INSTALLATION.md \
    LAUNCHER_GUIDE.md \
    README.md
```

### Method 2: Python Package Distribution

Build a Python package:

```bash
# Install build tools
pip install build twine

# Build the package
python -m build

# This creates:
# - dist/mft-professional-1.0.0.tar.gz
# - dist/mft_professional-1.0.0-py3-none-any.whl
```

### Method 3: Git Repository

Clone from repository:

```bash
git clone https://github.com/yourorg/mftapp.git
cd mftapp
```

---

## Distribution Package Contents

Your distribution should include:

```
mft-system-v1.0.0/
├── MFT_PRO/                          # Application code
│   ├── mft_system_with_rules_ui.py   # Main application
│   ├── auth_manager.py               # Authentication
│   ├── state_manager.py              # State persistence
│   ├── compliance_system.py          # Compliance framework
│   ├── mft_launcher_enhanced.py      # Desktop launcher
│   ├── requirements.txt              # Python dependencies
│   └── requirements_desktop.txt      # Desktop dependencies
├── setup.py                          # Python package setup
├── install.sh                        # Linux installer
├── install.bat                       # Windows installer
├── mft-system.service                # Systemd service file
├── config.env.template               # Configuration template
├── INSTALLATION.md                   # Installation guide
├── LAUNCHER_GUIDE.md                 # Launcher documentation
├── DISTRIBUTION.md                   # This file
└── README.md                         # Main documentation
```

---

## Transferring to Target Server

### Using SCP (Linux to Linux)

```bash
# Upload archive
scp mft-system-v1.0.0.tar.gz user@target-server:/tmp/

# SSH to target server
ssh user@target-server

# Extract and install
cd /tmp
tar -xzf mft-system-v1.0.0.tar.gz
cd mft-system-v1.0.0
sudo ./install.sh
```

### Using SFTP

```bash
sftp user@target-server
put mft-system-v1.0.0.tar.gz /tmp/
quit
```

### Using HTTP Download

1. Upload to a web server or file sharing service
2. On target server:

```bash
wget http://yourserver.com/mft-system-v1.0.0.tar.gz
# or
curl -O http://yourserver.com/mft-system-v1.0.0.tar.gz
```

### Using USB/Physical Media

1. Copy `mft-system-v1.0.0.tar.gz` to USB drive
2. Insert USB drive on target server
3. Copy and extract

---

## Deploying to Multiple Servers

### Using Ansible (Recommended for Enterprise)

Create an Ansible playbook `deploy-mft.yml`:

```yaml
---
- name: Deploy MFT Professional System
  hosts: mft_servers
  become: yes
  vars:
    mft_version: "1.0.0"
    mft_archive: "mft-system-v{{ mft_version }}.tar.gz"

  tasks:
    - name: Upload MFT archive
      copy:
        src: "{{ mft_archive }}"
        dest: "/tmp/{{ mft_archive }}"

    - name: Extract archive
      unarchive:
        src: "/tmp/{{ mft_archive }}"
        dest: /tmp/
        remote_src: yes

    - name: Run installation script
      command: /tmp/mft-system-v{{ mft_version }}/install.sh
      args:
        creates: /opt/mft-system

    - name: Start MFT service
      systemd:
        name: mft-system
        state: started
        enabled: yes
```

Run deployment:

```bash
ansible-playbook -i inventory deploy-mft.yml
```

### Using Docker (Containerized Deployment)

Create a `Dockerfile`:

```dockerfile
FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    libldap2-dev \
    libsasl2-dev \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /opt/mft-system

# Copy application files
COPY MFT_PRO/ .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Create data directories
RUN mkdir -p /var/lib/mft-system/{state,local_users,audit} \
    /var/log/mft-system

# Expose port
EXPOSE 5000

# Run application
CMD ["python", "mft_system_with_rules_ui.py"]
```

Build and run:

```bash
# Build image
docker build -t mft-system:1.0.0 .

# Run container
docker run -d \
    --name mft-system \
    -p 5000:5000 \
    -v /var/lib/mft-system:/var/lib/mft-system \
    -v /var/log/mft-system:/var/log/mft-system \
    mft-system:1.0.0
```

---

## Version Control & Updates

### Semantic Versioning

Use semantic versioning: `MAJOR.MINOR.PATCH`

- **MAJOR**: Breaking changes
- **MINOR**: New features (backwards compatible)
- **PATCH**: Bug fixes

### Creating a Release

1. Update version in `setup.py`
2. Create changelog entry
3. Tag the release:

```bash
git tag -a v1.0.0 -m "Release version 1.0.0"
git push origin v1.0.0
```

4. Create distribution packages
5. Upload to distribution server

### Upgrade Path

Include an `UPGRADE.md` with each release documenting:
- Breaking changes
- Migration steps
- Configuration changes
- Database schema updates

---

## Testing the Distribution

Before distributing, test on a clean system:

### Linux Testing (VM or Container)

```bash
# Create test container
docker run -it --rm ubuntu:22.04 /bin/bash

# Inside container, extract and install
cd /tmp
tar -xzf mft-system-v1.0.0.tar.gz
cd mft-system-v1.0.0
./install.sh

# Verify installation
systemctl status mft-system
curl http://localhost:5000
```

### Windows Testing (VM)

1. Create clean Windows VM
2. Copy installer
3. Run `install.bat` as Administrator
4. Verify service starts
5. Access web interface

### Checklist

- [ ] All dependencies install correctly
- [ ] Service starts without errors
- [ ] Web interface is accessible
- [ ] Can login with default credentials
- [ ] Can create transfer rule
- [ ] Logs are being written
- [ ] Configuration file is created
- [ ] Data directories are created with correct permissions

---

## Offline Installation

For servers without internet access:

### 1. Download All Dependencies

On a connected machine:

```bash
# Create downloads directory
mkdir mft-offline-install
cd mft-offline-install

# Download Python packages
pip download -r ../MFT_PRO/requirements.txt -d ./python-packages/

# Package everything
tar -czf mft-offline-v1.0.0.tar.gz \
    ../mft-system-v1.0.0.tar.gz \
    python-packages/
```

### 2. Install Offline

On target machine:

```bash
# Extract offline package
tar -xzf mft-offline-v1.0.0.tar.gz
tar -xzf mft-system-v1.0.0.tar.gz
cd mft-system-v1.0.0

# Modify install.sh to use local packages
# Change: pip install -r requirements.txt
# To: pip install --no-index --find-links=/path/to/python-packages -r requirements.txt
```

---

## Security Considerations

### Before Distribution

1. ✅ Remove any sensitive data from code
2. ✅ Remove development credentials
3. ✅ Remove debug flags
4. ✅ Review all default configurations
5. ✅ Scan for secrets in code
6. ✅ Update dependencies to latest secure versions

### Distribution Security

1. **Sign your packages**:
```bash
gpg --armor --detach-sign mft-system-v1.0.0.tar.gz
```

2. **Generate checksums**:
```bash
sha256sum mft-system-v1.0.0.tar.gz > mft-system-v1.0.0.tar.gz.sha256
```

3. **Verify on target**:
```bash
sha256sum -c mft-system-v1.0.0.tar.gz.sha256
gpg --verify mft-system-v1.0.0.tar.gz.asc mft-system-v1.0.0.tar.gz
```

---

## License and Legal

Ensure your distribution includes:

1. **LICENSE file** - Software license
2. **NOTICE file** - Third-party attributions
3. **COPYRIGHT** - Copyright information
4. **Terms of Service** - Usage terms

---

## Support for Customers

Provide these resources:

1. **Installation Guide** - `INSTALLATION.md`
2. **User Manual** - `README.md`
3. **Launcher Guide** - `LAUNCHER_GUIDE.md`
4. **FAQ Document**
5. **Support Contact** - Email/Phone/Portal
6. **Update Notification** - Subscription service

---

## Automation Scripts

### Auto-Deploy Script

Create `auto-deploy.sh`:

```bash
#!/bin/bash
# Auto-deploy to multiple servers

SERVERS=(
    "server1.example.com"
    "server2.example.com"
    "server3.example.com"
)

PACKAGE="mft-system-v1.0.0.tar.gz"
USER="admin"

for server in "${SERVERS[@]}"; do
    echo "Deploying to $server..."

    # Upload package
    scp "$PACKAGE" "$USER@$server:/tmp/"

    # Install
    ssh "$USER@$server" "
        cd /tmp
        tar -xzf $PACKAGE
        cd mft-system-v1.0.0
        sudo ./install.sh
        sudo systemctl start mft-system
    "

    echo "Deployment to $server complete"
done
```

---

## Monitoring Deployment

After deployment, verify:

```bash
# Check service status on all servers
for server in server1 server2 server3; do
    echo "Checking $server..."
    ssh $server "systemctl status mft-system"
done

# Check web interface
for server in server1 server2 server3; do
    curl -I "http://$server:5000"
done
```

---

## Rollback Plan

Always have a rollback plan:

1. Keep previous version installed as backup
2. Document rollback procedure
3. Test rollback before deployment
4. Have database backups
5. Keep configuration backups

Quick rollback:

```bash
sudo systemctl stop mft-system
sudo mv /opt/mft-system /opt/mft-system.new
sudo mv /opt/mft-system.backup /opt/mft-system
sudo systemctl start mft-system
```

---

## Questions?

For distribution and deployment questions:
- Email: deployment@yourcompany.com
- Documentation: https://docs.yourcompany.com/mft
- Support Portal: https://support.yourcompany.com
