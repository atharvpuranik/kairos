#!/usr/bin/env bash
# Validates the self-host stack end to end. Requires Docker Desktop and a
# root .env (copy .env.selfhost.example) pointing at a migrated Supabase project.
set -euo pipefail
cd "$(dirname "$0")/.."

fail() { echo "FAIL: $1"; docker compose logs --tail 30; docker compose down; exit 1; }

echo "== docker compose config sanity =="
docker compose config -q || fail "compose file invalid"

echo "== build + start =="
docker compose up -d --build

cleanup() { docker compose down; }
trap cleanup EXIT

echo "== waiting for API health =="
for i in $(seq 1 60); do
  if curl -fsS http://localhost:8000/health >/dev/null 2>&1; then break; fi
  [ "$i" = 60 ] && fail "API never became healthy"
  sleep 2
done
echo "API healthy"

echo "== dashboard serves /login =="
for i in $(seq 1 30); do
  if curl -fsS http://localhost:3000/login | grep -qi "sign in"; then break; fi
  [ "$i" = 30 ] && fail "dashboard /login not serving"
  sleep 2
done
echo "Dashboard serving"

echo "== API <-> SRH <-> Redis path (auth touches Redis; a clean 401 proves it) =="
code=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/v1/traces \
  -H "Content-Type: application/json" -H "Authorization: Bearer kai_live_bogus" \
  -d '{"pipeline_id":"00000000-0000-0000-0000-000000000000","query":"q","retrieved_chunks":[{"content":"c","score":1,"doc_id":"d"}],"final_answer":"a","latency_ms":1}')
[ "$code" = "401" ] || fail "expected 401 through the Redis auth path, got $code"
echo "Redis path OK (401 as expected)"

echo "== worker started without crashing =="
sleep 5
docker compose ps worker | grep -q "Up" || fail "worker container not running"
echo "Worker running"

echo ""
echo "ALL SELF-HOST CHECKS PASSED"
