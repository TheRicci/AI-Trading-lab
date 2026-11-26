#!/usr/bin/env bash
# deploy_aws.sh — Package AWS Lambda functions into build-aws/*.zip
# Prerequisites: bash, python3/pip, zip (or python3 -m zipfile), AWS CLI configured
#
# Usage:
#   ./scripts/deploy_aws.sh
#   cd terraform-aws && terraform apply

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FUNCTIONS_DIR="$PROJECT_ROOT/functions-aws"
BUILD_DIR="$PROJECT_ROOT/build-aws"

echo ""
echo "=== AWS Lambda Packaging ==="
echo "Project root: $PROJECT_ROOT"

mkdir -p "$BUILD_DIR"

FUNCTIONS=("bot-aws" "fed-scraper" "flow-scraper")

for func in "${FUNCTIONS[@]}"; do
    echo ""
    echo "Packaging $func..."
    func_dir="$FUNCTIONS_DIR/$func"
    pkg_dir="$(mktemp -d)"
    zip_path="$BUILD_DIR/${func}.zip"

    if [ ! -d "$func_dir" ]; then
        echo "  SKIP: $func_dir not found"
        rm -rf "$pkg_dir" || true
        continue
    fi

    req_file="$func_dir/requirements.txt"
    if [ -f "$req_file" ]; then
        echo "  Installing dependencies (Linux x86_64 Python 3.11)..."
        pip install -r "$req_file" -t "$pkg_dir" \
            --platform manylinux2014_x86_64 \
            --implementation cp \
            --python-version 3.11 \
            --only-binary=:all: \
            --quiet --upgrade
    fi

    # Copy function code (overwrite any same-named files from deps)
    find "$func_dir" -maxdepth 1 -type f -exec cp {} "$pkg_dir/" \;

    rm -f "$zip_path"
    (
        cd "$pkg_dir"
        if command -v zip >/dev/null 2>&1; then
            zip -rq "$zip_path" .
        else
            python -m zipfile -c "$zip_path" . 2>/dev/null || python3 -m zipfile -c "$zip_path" .
        fi
    )

    rm -rf "$pkg_dir" || true
    size_mb=$(du -h "$zip_path" | cut -f1)
    echo "  Created: $zip_path ($size_mb)"
done

echo ""
echo "=== Packaging Complete ==="
echo ""
echo "Next steps:"
echo "  1. Fill in terraform-aws/terraform.tfvars with your API keys"
echo "  2. cd terraform-aws"
echo "  3. terraform init"
echo "  4. terraform plan    (review the 9 Lambdas + schedules)"
echo "  5. terraform apply   (deploy everything)"
echo ""
echo "To get your Cosmos DB credentials for terraform.tfvars:"
echo "  cd ../terraform && terraform output -raw cosmos_endpoint"
echo "  cd ../terraform && terraform output -raw cosmos_primary_key"
echo ""
