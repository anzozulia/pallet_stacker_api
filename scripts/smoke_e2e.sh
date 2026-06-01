#!/usr/bin/env bash
# End-to-end smoke: submit a small pack job, poll until done, print the result.
# Assumes the stack is up (docker compose up) on localhost:8000.
set -euo pipefail
BASE="${1:-http://localhost:8000/api/v1}"

echo "== health ==" && curl -fsS "$BASE/health" && echo

REQ='{"boxes":[
  {"id":"A1","length":300,"width":200,"height":150,"weight":2,"rotations":"this_side_up"},
  {"id":"A2","length":300,"width":200,"height":150,"weight":2,"rotations":"this_side_up"},
  {"id":"A3","length":400,"width":300,"height":200,"weight":3,"rotations":"this_side_up"},
  {"id":"A4","length":250,"width":250,"height":250,"weight":1,"rotations":"all"}],
 "pallet":{"length":1200,"width":1000,"height":1500,"max_weight":500},
 "options":{"max_pallets":1,"time_budget_s":6}}'

echo "== submit =="
JOB=$(curl -fsS -X POST "$BASE/pack" -H 'content-type: application/json' -d "$REQ")
echo "$JOB"
JID=$(echo "$JOB" | python3 -c 'import sys,json;print(json.load(sys.stdin)["job_id"])')

echo "== poll =="
for i in $(seq 1 40); do
  R=$(curl -fsS "$BASE/jobs/$JID")
  S=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
  echo "  [$i] status=$S"
  if [ "$S" = "done" ] || [ "$S" = "failed" ] || [ "$S" = "timeout" ]; then
    echo "$R" | python3 -m json.tool | head -40
    break
  fi
  sleep 1
done

echo "== bad input (expect 400) =="
curl -s -o /dev/null -w "  HTTP %{http_code}\n" -X POST "$BASE/pack" \
  -H 'content-type: application/json' \
  -d '{"boxes":[{"id":"x","length":100.5,"width":100,"height":100}],"pallet":{"length":1000,"width":800,"height":600}}'
