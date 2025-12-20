# MFT Professional System - Packaging Guide

Complete guide for creating distributable packages from the MFT application source.

---

## Prerequisites

### Required Tools

Install these tools before creating distribution packages:

```bash
# Python packaging tools
pip install --upgrade pip setuptools wheel build twine

# Archive creation tools (usually pre-installed)
# Linux: tar, gzip, zip
# Windows: 7-Zip or built-in zip

# Optional: For signing packages
# gpg (GNU Privacy Guard)
```

### System Requirements

- **Python**: 3.8 or higher
- **Git**: For version control
- **Disk Space**: At least 500MB free
- **Internet**: For downloading dependencies (optional for offline packages)

---

## Quick Package Creation

### Method 1: Simple Archive (Recommended for most cases)

```bash
cd /home/user/mftapp

# Create tar.gz for Linux/macOS
tar -czf mft-system-v1.0.0.tar.gz \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='.gitignore' \
    --exclude='venv' \
    --exclude='*.log' \
    --exclude='.DS_Store' \
    --exclude='node_modules' \
    MFT_PRO/ \
    setup.py \
    install.sh \
    install.bat \
    mft-system.service \
    config.env.template \
    INSTALLATION.md \
    DISTRIBUTION.md \
    LAUNCHER_GUIDE.md \
    README.md

# Create zip for Windows (same files)
zip -r mft-system-v1.0.0.zip \
    --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.git/*' \
    --exclude='venv/*' \
    --exclude='*.log' \
    MFT_PRO/ \
    setup.py \
    install.sh \
    install.bat \
    mft-system.service \
    config.env.template \
    INSTALLATION.md \
    DISTRIBUTION.md \
    LAUNCHER_GUIDE.md \
    README.md

echo "✅ Packages created:"
echo "   - mft-system-v1.0.0.tar.gz (for Linux/macOS)"
echo "   - mft-system-v1.0.0.zip (for Windows)"
```

### Method 2: Python Wheel Package

```bash
cd /home/user/mftapp

# Install build tools if not already installed
pip install build

# Build the package (creates wheel and source distribution)
python -m build

# Output will be in dist/ directory:
# - dist/mft_professional-1.0.0-py3-none-any.whl
# - dist/mft-professional-1.0.0.tar.gz
```

---

## Detailed Packaging Process

### Step 1: Clean the Source Tree

```bash
cd /home/user/mftapp

# Remove compiled Python files
find . -type f -name '*.pyc' -delete
find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null

# Remove virtual environments
rm -rf venv/ MFT_PRO/venv/

# Remove log files
find . -type f -name '*.log' -delete

# Remove temporary files
rm -rf .pytest_cache/ .coverage htmlcov/

echo "✅ Source tree cleaned"
```

### Step 2: Verify File Structure

```bash
# Check that all required files are present
cat > /tmp/check_files.sh << 'EOF'
#!/bin/bash
FILES=(
    "MFT_PRO/mft_system_with_rules_ui.py"
    "MFT_PRO/auth_manager.py"
    "MFT_PRO/state_manager.py"
    "MFT_PRO/compliance_system.py"
    "MFT_PRO/requirements.txt"
    "setup.py"
    "install.sh"
    "install.bat"
    "INSTALLATION.md"
    "DISTRIBUTION.md"
)

echo "Checking required files..."
MISSING=0
for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        echo "✅ $file"
    else
        echo "❌ MISSING: $file"
        MISSING=$((MISSING + 1))
    fi
done

if [ $MISSING -eq 0 ]; then
    echo "✅ All required files present"
else
    echo "❌ $MISSING files missing"
    exit 1
fi
EOF

chmod +x /tmp/check_files.sh
/tmp/check_files.sh
```

### Step 3: Update Version Numbers

Update version in all relevant files:

```bash
# In setup.py
sed -i 's/version=".*"/version="1.0.0"/' setup.py

# In INSTALLATION.md (if version is mentioned)
# Manually update version references

echo "✅ Version numbers updated"
```

### Step 4: Create Distribution Archive

```bash
#!/bin/bash
# Save as: create_distribution.sh

VERSION="1.0.0"
PACKAGE_NAME="mft-system-v${VERSION}"
BUILD_DIR="/tmp/mft-build"

# Clean build directory
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/$PACKAGE_NAME"

# Copy files to build directory
echo "Copying files..."
rsync -av --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='venv' \
    --exclude='*.log' \
    --exclude='.pytest_cache' \
    /home/user/mftapp/MFT_PRO/ \
    "$BUILD_DIR/$PACKAGE_NAME/MFT_PRO/"

# Copy root files
cp /home/user/mftapp/setup.py "$BUILD_DIR/$PACKAGE_NAME/"
cp /home/user/mftapp/install.sh "$BUILD_DIR/$PACKAGE_NAME/"
cp /home/user/mftapp/install.bat "$BUILD_DIR/$PACKAGE_NAME/"
cp /home/user/mftapp/mft-system.service "$BUILD_DIR/$PACKAGE_NAME/"
cp /home/user/mftapp/config.env.template "$BUILD_DIR/$PACKAGE_NAME/"
cp /home/user/mftapp/*.md "$BUILD_DIR/$PACKAGE_NAME/"

# Set execute permissions
chmod +x "$BUILD_DIR/$PACKAGE_NAME/install.sh"

# Create archives
cd "$BUILD_DIR"

echo "Creating tar.gz..."
tar -czf "${PACKAGE_NAME}.tar.gz" "$PACKAGE_NAME"

echo "Creating zip..."
zip -r "${PACKAGE_NAME}.zip" "$PACKAGE_NAME"

# Move to output directory
OUTPUT_DIR="/home/user/mftapp/dist"
mkdir -p "$OUTPUT_DIR"
mv "${PACKAGE_NAME}.tar.gz" "$OUTPUT_DIR/"
mv "${PACKAGE_NAME}.zip" "$OUTPUT_DIR/"

echo ""
echo "✅ Distribution packages created:"
echo "   📦 $OUTPUT_DIR/${PACKAGE_NAME}.tar.gz"
echo "   📦 $OUTPUT_DIR/${PACKAGE_NAME}.zip"
echo ""
echo "Package size:"
ls -lh "$OUTPUT_DIR/${PACKAGE_NAME}".{tar.gz,zip}
```

### Step 5: Create Offline Package (with dependencies)

```bash
#!/bin/bash
# Save as: create_offline_package.sh

VERSION="1.0.0"
PACKAGE_NAME="mft-system-offline-v${VERSION}"
BUILD_DIR="/tmp/mft-offline-build"

# Clean and create build directory
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/$PACKAGE_NAME"

# Download all Python dependencies
echo "Downloading Python dependencies..."
mkdir -p "$BUILD_DIR/$PACKAGE_NAME/python-packages"
pip download -r /home/user/mftapp/MFT_PRO/requirements.txt \
    -d "$BUILD_DIR/$PACKAGE_NAME/python-packages"

# Download desktop dependencies
pip download -r /home/user/mftapp/MFT_PRO/requirements_desktop.txt \
    -d "$BUILD_DIR/$PACKAGE_NAME/python-packages-desktop" 2>/dev/null || true

# Copy application files
rsync -av --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='venv' \
    /home/user/mftapp/MFT_PRO/ \
    "$BUILD_DIR/$PACKAGE_NAME/MFT_PRO/"

# Copy installation files
cp /home/user/mftapp/setup.py "$BUILD_DIR/$PACKAGE_NAME/"
cp /home/user/mftapp/install.sh "$BUILD_DIR/$PACKAGE_NAME/"
cp /home/user/mftapp/install.bat "$BUILD_DIR/$PACKAGE_NAME/"
cp /home/user/mftapp/mft-system.service "$BUILD_DIR/$PACKAGE_NAME/"
cp /home/user/mftapp/config.env.template "$BUILD_DIR/$PACKAGE_NAME/"
cp /home/user/mftapp/*.md "$BUILD_DIR/$PACKAGE_NAME/"

# Create offline installation instructions
cat > "$BUILD_DIR/$PACKAGE_NAME/OFFLINE_INSTALL.txt" << 'EOF'
OFFLINE INSTALLATION INSTRUCTIONS
==================================

This package includes all Python dependencies for offline installation.

Linux Installation:
1. Extract the package:
   tar -xzf mft-system-offline-v1.0.0.tar.gz
   cd mft-system-offline-v1.0.0

2. Modify install.sh to use local packages:
   - Find the line: pip install -r requirements.txt
   - Replace with: pip install --no-index --find-links=python-packages -r requirements.txt

3. Run installation:
   sudo ./install.sh

Windows Installation:
1. Extract the package
2. Modify install.bat similarly
3. Run as Administrator

Note: System dependencies (build tools, LDAP libraries) must still be
installed from OS repositories or installation media.
EOF

# Set permissions
chmod +x "$BUILD_DIR/$PACKAGE_NAME/install.sh"

# Create archive
cd "$BUILD_DIR"
tar -czf "${PACKAGE_NAME}.tar.gz" "$PACKAGE_NAME"

# Move to output
OUTPUT_DIR="/home/user/mftapp/dist"
mkdir -p "$OUTPUT_DIR"
mv "${PACKAGE_NAME}.tar.gz" "$OUTPUT_DIR/"

echo ""
echo "✅ Offline package created:"
echo "   📦 $OUTPUT_DIR/${PACKAGE_NAME}.tar.gz"
echo ""
ls -lh "$OUTPUT_DIR/${PACKAGE_NAME}.tar.gz"
```

---

## Package Verification

### Step 1: Generate Checksums

```bash
cd /home/user/mftapp/dist

# Generate SHA256 checksums
sha256sum mft-system-v*.tar.gz > checksums.txt
sha256sum mft-system-v*.zip >> checksums.txt

# Display checksums
echo "✅ Checksums generated:"
cat checksums.txt
```

### Step 2: Sign Packages (Optional)

```bash
# Generate GPG key if you don't have one
gpg --gen-key

# Sign the packages
gpg --armor --detach-sign mft-system-v1.0.0.tar.gz
gpg --armor --detach-sign mft-system-v1.0.0.zip

# Verify signatures
gpg --verify mft-system-v1.0.0.tar.gz.asc mft-system-v1.0.0.tar.gz
gpg --verify mft-system-v1.0.0.zip.asc mft-system-v1.0.0.zip
```

### Step 3: Test the Package

```bash
# Extract to temporary location
cd /tmp
tar -xzf /home/user/mftapp/dist/mft-system-v1.0.0.tar.gz
cd mft-system-v1.0.0

# Verify all files are present
ls -la

# Test installation script (dry run)
bash -n install.sh  # Check for syntax errors

echo "✅ Package verification complete"
```

---

## Dependencies Reference

### Build-Time Dependencies

Install these on the **packaging machine**:

```bash
# Python packaging tools
pip install --upgrade pip setuptools wheel build twine

# For offline packages
pip install pip-tools

# Optional: Code quality tools
pip install black flake8 pytest
```

### Run-Time Dependencies (Included in package)

These are installed automatically during installation via `requirements.txt`:

**Core Dependencies:**
- Flask >= 2.3.0
- Werkzeug >= 2.3.0
- paramiko >= 3.3.0
- pysmb >= 1.2.9
- ldap3 >= 2.9.0
- reportlab >= 4.0.0
- watchdog >= 3.0.0
- cryptography >= 41.0.0
- psutil >= 5.9.0

**Full list**: See `/home/user/mftapp/MFT_PRO/requirements.txt`

### System Dependencies (OS-level)

These must be installed from OS package manager:

**Linux (Ubuntu/Debian):**
```bash
sudo apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    libldap2-dev \
    libsasl2-dev \
    git
```

**Linux (RHEL/CentOS):**
```bash
sudo yum install -y \
    python3 \
    python3-pip \
    python3-devel \
    gcc \
    openldap-devel
```

**Windows:**
- Python 3.8+ (from python.org)
- Microsoft Visual C++ Build Tools (for some packages)

---

## Complete Packaging Script

Save this as `package.sh`:

```bash
#!/bin/bash
#
# Complete MFT System Packaging Script
# Creates all distribution packages
#

set -e  # Exit on error

VERSION="1.0.0"
SOURCE_DIR="/home/user/mftapp"
BUILD_DIR="/tmp/mft-package-build"
DIST_DIR="$SOURCE_DIR/dist"

echo "=========================================="
echo "MFT System - Package Builder"
echo "Version: $VERSION"
echo "=========================================="
echo ""

# Clean previous builds
echo "Cleaning previous builds..."
rm -rf "$BUILD_DIR" "$DIST_DIR"
mkdir -p "$BUILD_DIR" "$DIST_DIR"

# Clean source tree
echo "Cleaning source tree..."
cd "$SOURCE_DIR"
find . -type f -name '*.pyc' -delete
find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find . -type f -name '*.log' -delete

# Create standard package
echo ""
echo "Creating standard package..."
PACKAGE_NAME="mft-system-v${VERSION}"
PACKAGE_DIR="$BUILD_DIR/$PACKAGE_NAME"

mkdir -p "$PACKAGE_DIR"

# Copy files
rsync -av --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='venv' \
    --exclude='*.log' \
    --exclude='dist' \
    --exclude='.pytest_cache' \
    "$SOURCE_DIR/MFT_PRO/" "$PACKAGE_DIR/MFT_PRO/"

cp "$SOURCE_DIR/setup.py" "$PACKAGE_DIR/"
cp "$SOURCE_DIR/install.sh" "$PACKAGE_DIR/"
cp "$SOURCE_DIR/install.bat" "$PACKAGE_DIR/"
cp "$SOURCE_DIR/mft-system.service" "$PACKAGE_DIR/"
cp "$SOURCE_DIR/config.env.template" "$PACKAGE_DIR/"
cp "$SOURCE_DIR"/*.md "$PACKAGE_DIR/"

chmod +x "$PACKAGE_DIR/install.sh"

# Create archives
cd "$BUILD_DIR"
echo "Creating tar.gz..."
tar -czf "${PACKAGE_NAME}.tar.gz" "$PACKAGE_NAME"

echo "Creating zip..."
zip -r -q "${PACKAGE_NAME}.zip" "$PACKAGE_NAME"

mv *.tar.gz "$DIST_DIR/"
mv *.zip "$DIST_DIR/"

# Create offline package
echo ""
echo "Creating offline package..."
OFFLINE_NAME="mft-system-offline-v${VERSION}"
OFFLINE_DIR="$BUILD_DIR/$OFFLINE_NAME"

mkdir -p "$OFFLINE_DIR/python-packages"

# Download dependencies
echo "Downloading Python dependencies..."
pip download -q -r "$SOURCE_DIR/MFT_PRO/requirements.txt" \
    -d "$OFFLINE_DIR/python-packages"

# Copy application
rsync -av --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='venv' \
    "$SOURCE_DIR/MFT_PRO/" "$OFFLINE_DIR/MFT_PRO/"

cp "$SOURCE_DIR/setup.py" "$OFFLINE_DIR/"
cp "$SOURCE_DIR/install.sh" "$OFFLINE_DIR/"
cp "$SOURCE_DIR/install.bat" "$OFFLINE_DIR/"
cp "$SOURCE_DIR/mft-system.service" "$OFFLINE_DIR/"
cp "$SOURCE_DIR/config.env.template" "$OFFLINE_DIR/"
cp "$SOURCE_DIR"/*.md "$OFFLINE_DIR/"

chmod +x "$OFFLINE_DIR/install.sh"

# Create offline package archive
cd "$BUILD_DIR"
tar -czf "${OFFLINE_NAME}.tar.gz" "$OFFLINE_NAME"
mv "${OFFLINE_NAME}.tar.gz" "$DIST_DIR/"

# Generate checksums
echo ""
echo "Generating checksums..."
cd "$DIST_DIR"
sha256sum *.tar.gz *.zip > checksums.sha256

# Display results
echo ""
echo "=========================================="
echo "✅ Packaging Complete!"
echo "=========================================="
echo ""
echo "Packages created in: $DIST_DIR"
echo ""
ls -lh "$DIST_DIR"
echo ""
echo "Checksums:"
cat "$DIST_DIR/checksums.sha256"
echo ""
echo "To distribute:"
echo "  1. Test the package on a clean system"
echo "  2. Upload to distribution server"
echo "  3. Update download links in documentation"
echo ""
```

Make it executable and run:

```bash
chmod +x package.sh
./package.sh
```

---

## Quick Reference

### Essential Commands

```bash
# Install build tools
pip install build wheel twine

# Clean workspace
find . -name '*.pyc' -delete
find . -name '__pycache__' -delete

# Create package (simple)
tar -czf mft-system.tar.gz MFT_PRO/ *.sh *.bat *.service *.py *.md

# Create package (with Python build)
python -m build

# Generate checksums
sha256sum mft-system-*.tar.gz > checksums.txt

# Test package
tar -tzf mft-system-v1.0.0.tar.gz | head -20
```

### Package Locations

After running package.sh:
- **Standard packages**: `dist/mft-system-v1.0.0.tar.gz`, `.zip`
- **Offline package**: `dist/mft-system-offline-v1.0.0.tar.gz`
- **Checksums**: `dist/checksums.sha256`
- **Python wheel**: `dist/mft_professional-1.0.0-py3-none-any.whl`

---

## Troubleshooting

### "pip: command not found"
```bash
# Install pip
curl https://bootstrap.pypa.io/get-pip.py -o get-pip.py
python3 get-pip.py
```

### "tar: command not found" (Windows)
- Use 7-Zip or WinRAR
- Or install Git Bash which includes tar

### "Permission denied" on install.sh
```bash
chmod +x install.sh
```

### Package too large
- Remove unnecessary files
- Check for venv or __pycache__ directories
- Verify excludes in tar command

---

All scripts and commands are ready to use! 🎯
