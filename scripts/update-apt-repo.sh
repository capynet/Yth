#!/bin/bash
# Update APT repository indices
# Usage: ./scripts/update-apt-repo.sh [path-to-repo-root]
#
# This script regenerates the APT repository metadata files.
# Run this after adding new .deb files to the pool directory.
#
# Prerequisites:
# - dpkg-scanpackages (from dpkg-dev package)
# - GPG key "Capynet APT Repository" imported

set -e

REPO_ROOT="${1:-.}"
cd "$REPO_ROOT"

echo "=== Updating APT Repository ==="
echo "Repository root: $(pwd)"

# Verify pool exists
if [ ! -d "pool/main/t/tubesync" ]; then
    echo "Error: pool/main/t/tubesync directory not found"
    echo "Create it and add .deb files first"
    exit 1
fi

# List packages
echo ""
echo "Packages in pool:"
ls -la pool/main/t/tubesync/*.deb 2>/dev/null || echo "  (none)"

# Create dists structure
mkdir -p dists/stable/main/binary-amd64

# Generate Packages file
echo ""
echo "Generating Packages index..."
dpkg-scanpackages --arch amd64 pool/ > dists/stable/main/binary-amd64/Packages
gzip -9c dists/stable/main/binary-amd64/Packages > dists/stable/main/binary-amd64/Packages.gz

echo "  Packages: $(wc -l < dists/stable/main/binary-amd64/Packages) lines"

# Generate Release file
echo ""
echo "Generating Release file..."
cd dists/stable

cat > Release << EOF
Origin: Capynet
Label: Capynet APT Repository
Suite: stable
Codename: stable
Architectures: amd64
Components: main
Description: Capynet APT Repository for tubesync
Date: $(date -Ru)
EOF

# Add checksums
echo "MD5Sum:" >> Release
for f in main/binary-amd64/Packages main/binary-amd64/Packages.gz; do
    if [ -f "$f" ]; then
        echo " $(md5sum $f | cut -d' ' -f1) $(wc -c < $f) $f" >> Release
    fi
done

echo "SHA256:" >> Release
for f in main/binary-amd64/Packages main/binary-amd64/Packages.gz; do
    if [ -f "$f" ]; then
        echo " $(sha256sum $f | cut -d' ' -f1) $(wc -c < $f) $f" >> Release
    fi
done

cd - > /dev/null

# Sign if GPG key available
if gpg --list-secret-keys "Capynet APT Repository" &>/dev/null; then
    echo ""
    echo "Signing Release file..."
    gpg --batch --yes --armor --detach-sign -o dists/stable/Release.gpg dists/stable/Release
    gpg --batch --yes --armor --clearsign -o dists/stable/InRelease dists/stable/Release
    echo "  Created Release.gpg and InRelease"
else
    echo ""
    echo "Warning: GPG key 'Capynet APT Repository' not found"
    echo "Release file will not be signed"
fi

echo ""
echo "=== APT Repository Updated ==="
echo ""
echo "Structure:"
find dists pool -type f 2>/dev/null | head -20
