#!/bin/bash
#
# MFT System - Complete Package Builder
# Creates distribution packages ready for deployment
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
echo "📦 Cleaning previous builds..."
rm -rf "$BUILD_DIR" "$DIST_DIR"
mkdir -p "$BUILD_DIR" "$DIST_DIR"

# Clean source tree
echo "🧹 Cleaning source tree..."
cd "$SOURCE_DIR"
find . -type f -name '*.pyc' -delete 2>/dev/null || true
find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
find . -type f -name '*.log' -delete 2>/dev/null || true

echo "✅ Clean complete"
echo ""

# ============================================================================
# Standard Package
# ============================================================================

echo "📦 Creating standard package..."
PACKAGE_NAME="mft-system-v${VERSION}"
PACKAGE_DIR="$BUILD_DIR/$PACKAGE_NAME"

mkdir -p "$PACKAGE_DIR"

# Copy application files
echo "   Copying application files..."
rsync -a --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='.gitignore' \
    --exclude='venv' \
    --exclude='*.log' \
    --exclude='dist' \
    --exclude='.pytest_cache' \
    --exclude='*.egg-info' \
    "$SOURCE_DIR/MFT_PRO/" "$PACKAGE_DIR/MFT_PRO/"

# Copy installation files
echo "   Copying installation files..."
cp "$SOURCE_DIR/setup.py" "$PACKAGE_DIR/"
cp "$SOURCE_DIR/install.sh" "$PACKAGE_DIR/"
cp "$SOURCE_DIR/install.bat" "$PACKAGE_DIR/"
cp "$SOURCE_DIR/mft-system.service" "$PACKAGE_DIR/"
cp "$SOURCE_DIR/config.env.template" "$PACKAGE_DIR/"

# Copy documentation
echo "   Copying documentation..."
cp "$SOURCE_DIR"/*.md "$PACKAGE_DIR/" 2>/dev/null || true

# Set permissions
chmod +x "$PACKAGE_DIR/install.sh"

# Create archives
cd "$BUILD_DIR"
echo "   Creating tar.gz archive..."
tar -czf "${PACKAGE_NAME}.tar.gz" "$PACKAGE_NAME"

echo "   Creating zip archive..."
zip -r -q "${PACKAGE_NAME}.zip" "$PACKAGE_NAME"

# Move to dist
mv "${PACKAGE_NAME}.tar.gz" "$DIST_DIR/"
mv "${PACKAGE_NAME}.zip" "$DIST_DIR/"

echo "✅ Standard package created"
echo ""

# ============================================================================
# Offline Package (with Python dependencies)
# ============================================================================

echo "📦 Creating offline package..."
OFFLINE_NAME="mft-system-offline-v${VERSION}"
OFFLINE_DIR="$BUILD_DIR/$OFFLINE_NAME"

mkdir -p "$OFFLINE_DIR/python-packages"

# Download Python dependencies
echo "   Downloading Python dependencies..."
if command -v pip &> /dev/null; then
    pip download -q -r "$SOURCE_DIR/MFT_PRO/requirements.txt" \
        -d "$OFFLINE_DIR/python-packages" 2>/dev/null || \
        echo "   ⚠️  Warning: Some dependencies may have failed to download"
else
    echo "   ⚠️  Warning: pip not found, skipping dependency download"
fi

# Copy application files
echo "   Copying application files..."
rsync -a --exclude='*.pyc' \
    --exclude='__pycache__' \
    --exclude='.git' \
    --exclude='venv' \
    --exclude='*.log' \
    "$SOURCE_DIR/MFT_PRO/" "$OFFLINE_DIR/MFT_PRO/"

# Copy installation files
cp "$SOURCE_DIR/setup.py" "$OFFLINE_DIR/"
cp "$SOURCE_DIR/install.sh" "$OFFLINE_DIR/"
cp "$SOURCE_DIR/install.bat" "$OFFLINE_DIR/"
cp "$SOURCE_DIR/mft-system.service" "$OFFLINE_DIR/"
cp "$SOURCE_DIR/config.env.template" "$OFFLINE_DIR/"
cp "$SOURCE_DIR"/*.md "$OFFLINE_DIR/" 2>/dev/null || true

chmod +x "$OFFLINE_DIR/install.sh"

# Create offline installation instructions
cat > "$OFFLINE_DIR/OFFLINE_INSTALL.txt" << 'EOF'
========================================
OFFLINE INSTALLATION INSTRUCTIONS
========================================

This package includes all Python dependencies for offline installation.

LINUX INSTALLATION:
-------------------
1. Extract the package:
   tar -xzf mft-system-offline-v1.0.0.tar.gz
   cd mft-system-offline-v1.0.0

2. Edit install.sh to use local packages:
   - Find the line: pip install -r requirements.txt
   - Replace with:
     pip install --no-index --find-links=python-packages -r requirements.txt

3. Run installation:
   sudo ./install.sh

WINDOWS INSTALLATION:
--------------------
1. Extract the package
2. Edit install.bat similarly to use local packages
3. Run install.bat as Administrator

NOTE: System dependencies (build tools, LDAP libraries) must still
be installed from OS repositories or installation media.
EOF

# Create offline package archive
echo "   Creating offline archive..."
cd "$BUILD_DIR"
tar -czf "${OFFLINE_NAME}.tar.gz" "$OFFLINE_NAME"
mv "${OFFLINE_NAME}.tar.gz" "$DIST_DIR/"

echo "✅ Offline package created"
echo ""

# ============================================================================
# Generate Checksums and Summary
# ============================================================================

echo "🔐 Generating checksums..."
cd "$DIST_DIR"
sha256sum *.tar.gz *.zip > checksums.sha256 2>/dev/null || \
    shasum -a 256 *.tar.gz *.zip > checksums.sha256

echo "✅ Checksums generated"
echo ""

# ============================================================================
# Display Results
# ============================================================================

echo "=========================================="
echo "✅ Packaging Complete!"
echo "=========================================="
echo ""
echo "📦 Packages created in: $DIST_DIR"
echo ""
echo "Files:"
echo "------"
ls -lh "$DIST_DIR" | grep -v total
echo ""
echo "Checksums:"
echo "----------"
cat "$DIST_DIR/checksums.sha256"
echo ""
echo "Package sizes:"
echo "--------------"
du -h "$DIST_DIR"/* | sort -h
echo ""
echo "=========================================="
echo "Next Steps:"
echo "=========================================="
echo ""
echo "1. Test the package:"
echo "   cd /tmp"
echo "   tar -xzf $DIST_DIR/mft-system-v${VERSION}.tar.gz"
echo "   cd mft-system-v${VERSION}"
echo "   sudo ./install.sh"
echo ""
echo "2. Transfer to target server:"
echo "   scp $DIST_DIR/mft-system-v${VERSION}.tar.gz user@server:/tmp/"
echo ""
echo "3. Verify checksum on target:"
echo "   sha256sum -c checksums.sha256"
echo ""
echo "=========================================="
