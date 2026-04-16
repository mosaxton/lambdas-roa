#!/usr/bin/env bash
# Build the roa-shared Lambda Layer zip manually (without sam build).
# Use this for inspection or uploading the layer outside of a full SAM deploy.
#
# Usage: ./scripts/build-layer.sh [output_dir]
# Output: <output_dir>/roa-shared-layer.zip  (default: .aws-sam/layers)

set -euo pipefail

OUTPUT_DIR="${1:-.aws-sam/layers}"
LAYER_ZIP="${OUTPUT_DIR}/roa-shared-layer.zip"
BUILD_DIR="$(mktemp -d)"
PYTHON_DIR="${BUILD_DIR}/python"

echo "==> Building roa-shared Lambda Layer"
echo "    Source:  shared/"
echo "    Output:  ${LAYER_ZIP}"

# Install runtime dependencies into the layer's python/ directory
echo "==> Installing runtime deps (shared/requirements.txt)..."
pip install \
  --quiet \
  --target "${PYTHON_DIR}" \
  --platform manylinux2014_x86_64 \
  --implementation cp \
  --python-version 3.12 \
  --only-binary :all: \
  -r shared/requirements.txt

# Copy the shared Python package into python/shared/
echo "==> Copying shared/ package into layer..."
mkdir -p "${PYTHON_DIR}/shared"
cp shared/__init__.py "${PYTHON_DIR}/shared/"
for f in shared/*.py; do
  [[ "$(basename "$f")" != "__init__.py" ]] && cp "$f" "${PYTHON_DIR}/shared/"
done

# Zip it up
mkdir -p "${OUTPUT_DIR}"
(cd "${BUILD_DIR}" && zip -r9 - python) > "${LAYER_ZIP}"

# Cleanup
rm -rf "${BUILD_DIR}"

echo "==> Done: ${LAYER_ZIP} ($(du -sh "${LAYER_ZIP}" | cut -f1))"
