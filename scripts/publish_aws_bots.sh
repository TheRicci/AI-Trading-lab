#!/usr/bin/env bash
# publish_aws_bots.sh — Re-package and push code to all AWS Lambda bots
# AWS equivalent of `func azure functionapp publish`.
# Use for code-only changes. For infra/prompt/model changes, use `terraform apply`.
#
# Usage:
#   ./scripts/publish_aws_bots.sh            # update all 9 Lambdas
#   ./scripts/publish_aws_bots.sh bot        # update only the 7 bot Lambdas
#   ./scripts/publish_aws_bots.sh fed        # update only fed-scraper
#   ./scripts/publish_aws_bots.sh flow       # update only flow-scraper

set -euo pipefail

ONLY="${1:-all}"
case "$ONLY" in
    all|bot|fed|flow) ;;
    *)
        echo "Usage: $0 [all|bot|fed|flow]" >&2
        exit 1
        ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FUNCTIONS_DIR="$PROJECT_ROOT/functions-aws"
BUILD_DIR="$PROJECT_ROOT/build-aws"

package_lambda() {
    local name="$1"
    local func_dir="$FUNCTIONS_DIR/$name"
    local pkg_dir
    pkg_dir="$(mktemp -d)"
    local zip_path="$BUILD_DIR/${name}.zip"

    local req_file="$func_dir/requirements.txt"
    if [ -f "$req_file" ]; then
        pip install -r "$req_file" -t "$pkg_dir" \
            --platform manylinux2014_x86_64 \
            --implementation cp \
            --python-version 3.11 \
            --only-binary=:all: \
            --quiet --upgrade
    fi

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

    local size_mb
    size_mb=$(du -h "$zip_path" | cut -f1)
    echo "  Packaged $name ($size_mb)"
}

get_lambda_names() {
    local prefix="$1"
    aws lambda list-functions \
        --query "Functions[?starts_with(FunctionName, \`${prefix}\`)].FunctionName" \
        --output text | tr '\t' '\n' | sed '/^$/d'
}

publish_lambda() {
    local zip_path="$1"
    local fn_name="$2"
    if command -v cygpath >/dev/null 2>&1; then
        zip_path="$(cygpath -m "$zip_path")"
    fi
    printf "  Updating %s..." "$fn_name"
    if aws lambda update-function-code \
        --function-name "$fn_name" \
        --zip-file "fileb://$zip_path" \
        --output json >/dev/null; then
        echo " OK"
    else
        echo " FAILED"
        return 1
    fi
}

mkdir -p "$BUILD_DIR"

echo ""
echo "=== AWS Lambda Publish ==="

targets=()
if [ "$ONLY" = "all" ] || [ "$ONLY" = "bot" ];  then targets+=("bot-aws"); fi
if [ "$ONLY" = "all" ] || [ "$ONLY" = "fed" ];  then targets+=("fed-scraper"); fi
if [ "$ONLY" = "all" ] || [ "$ONLY" = "flow" ]; then targets+=("flow-scraper"); fi

for target in "${targets[@]}"; do
    echo ""
    echo "[$target]"
    package_lambda "$target"
    zip_path="$BUILD_DIR/${target}.zip"

    if [ "$target" = "bot-aws" ]; then
        prefix="bot-"
    else
        prefix="$target"
    fi

    names=$(get_lambda_names "$prefix" || true)
    if [ -z "$names" ]; then
        echo "  No ${prefix}* Lambda functions found. Deploy with terraform first."
        continue
    fi

    while IFS= read -r fn_name; do
        [ -n "$fn_name" ] && publish_lambda "$zip_path" "$fn_name"
    done <<< "$names"
done

echo ""
echo "=== Publish Complete ==="
