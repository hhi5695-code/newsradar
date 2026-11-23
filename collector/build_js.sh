#!/bin/bash
# Rebuild the obfuscated page script from site/js/src/app.js.
set -euo pipefail
cd "$(dirname "$0")/.."
npx --yes javascript-obfuscator@4.1.1 site/js/src/app.js --output site/js/app.js \
  --compact true \
  --control-flow-flattening false \
  --dead-code-injection false \
  --string-array true \
  --string-array-encoding base64 \
  --string-array-threshold 0.75 \
  --unicode-escape-sequence false \
  --self-defending false \
  --rename-globals false \
  --identifier-names-generator hexadecimal \
  --simplify true
