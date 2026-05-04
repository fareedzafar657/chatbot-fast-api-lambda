#!/bin/bash
# Run this on your local machine or in CloudShell to package and deploy.
# Usage: bash deploy.sh <function-name> <region>
#
# Example:
#   bash deploy.sh chatbot-api us-east-1

set -e

FUNCTION_NAME=${1:-"chatbot-api"}
REGION=${2:-"us-east-1"}
BUILD_DIR=".build"
ZIP_FILE="function.zip"

echo "==> Cleaning build dir..."
rm -rf $BUILD_DIR $ZIP_FILE
mkdir -p $BUILD_DIR

echo "==> Installing dependencies into build dir..."
pip install -r requirements.txt --target $BUILD_DIR --upgrade --quiet

echo "==> Copying app code..."
cp -r app lambda_handler.py $BUILD_DIR/

echo "==> Zipping..."
cd $BUILD_DIR
zip -r ../$ZIP_FILE . -q
cd ..

echo "==> Deploying to Lambda: $FUNCTION_NAME..."
aws lambda update-function-code \
  --function-name $FUNCTION_NAME \
  --zip-file fileb://$ZIP_FILE \
  --region $REGION

echo "==> Done! Zip size: $(du -sh $ZIP_FILE | cut -f1)"
