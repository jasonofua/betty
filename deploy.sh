#!/bin/sh
# Deploy to Railway and VERIFY the new build is actually serving.
# Without this, `railway up` returns while the old container still answers -
# so a run dispatched immediately afterwards executes the previous code.
set -e
cd "$(dirname "$0")"
SHA=$(git rev-parse --short HEAD)
echo "$SHA $(date -u +%Y-%m-%dT%H:%MZ)" > BUILD_STAMP
git add BUILD_STAMP >/dev/null 2>&1 || true
git commit -q -m "deploy stamp $SHA" >/dev/null 2>&1 || true
git push -q origin main >/dev/null 2>&1 || true
railway up --detach >/dev/null
echo "deploying $SHA ..."
URL=https://betty-production-ea69.up.railway.app/api/status
i=0
while [ $i -lt 60 ]; do
  LIVE=$(curl -s --max-time 8 "$URL" 2>/dev/null | sed -n 's/.*"build": "\([^ ]*\).*/\1/p')
  if [ "$LIVE" = "$SHA" ]; then echo "LIVE: $SHA confirmed serving"; exit 0; fi
  sleep 15; i=$((i+1))
done
echo "TIMEOUT: still serving '$LIVE', expected '$SHA'"; exit 1
