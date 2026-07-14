#!/usr/bin/env bash
# ============================================================================
# Pre-push security scan for a PUBLIC repo.  See ../STRICT-INSTRUCTIONS.md.
#   exit 0  → safe to push
#   exit 1  → STOP: a possible secret / leak was found; do NOT push.
# Runs locally (pre-push hook) AND in CI (blocks merge).
# ============================================================================
set -uo pipefail

fail=0
say() { printf '%s\n' "$*"; }
bad() { printf '  \342\234\227 %s\n' "$*"; fail=1; }

# ---- files to scan: tracked if in a git repo, else the working tree ----
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  mapfile -t FILES < <(git ls-files)
else
  mapfile -t FILES < <(find . -type f -not -path './.git/*')
fi

# meta files that legitimately describe secret patterns → skip content scan
skip_content() {
  case "$1" in
    STRICT-INSTRUCTIONS.md|*/STRICT-INSTRUCTIONS.md) return 0 ;;
    scripts/security-check.sh|*/security-check.sh)   return 0 ;;
    *) return 1 ;;
  esac
}

# ---- 1) secret-bearing FILES must never be tracked ----
say "> Checking for secret-bearing files..."
for f in "${FILES[@]}"; do
  case "$f" in
    .env|.env.*|*.env|*.env.*|*.pem|*.key|*id_rsa*|*credentials*.json|*service-account*.json|*.p12|*.pfx)
      bad "forbidden file tracked: $f  (must never be committed)" ;;
  esac
done

# ---- 2) high-confidence secret CONTENT patterns ----
say "> Scanning content for secrets..."
PATTERNS='eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}|gh[posur]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{40,}|sk-[A-Za-z0-9]{32,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----'
for f in "${FILES[@]}"; do
  skip_content "$f" && continue
  [ -f "$f" ] || continue
  while IFS= read -r hit; do
    [ -n "$hit" ] && bad "possible secret in $f (line ${hit%%:*})"
  done < <(grep -EnI "$PATTERNS" "$f" 2>/dev/null)
done

# ---- 3) hardcoded credential assignments (not env / secret-store reads) ----
say "> Scanning for hardcoded credential assignments..."
ASSIGN='(SERVICE_ROLE_KEY|SUPABASE_[A-Z_]*KEY|SECRET_KEY|ACCESS_TOKEN|API_KEY|AUTH_TOKEN|PASSWORD)[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"']{12,}'
IGNORE='os\.environ|process\.env|secrets\.|vault|getenv|System\.getenv|<[^>]+>|xxxx|redacted|example|placeholder|dummy'
for f in "${FILES[@]}"; do
  skip_content "$f" && continue
  [ -f "$f" ] || continue
  while IFS= read -r hit; do
    [ -n "$hit" ] && bad "hardcoded credential in $f (line ${hit%%:*})"
  done < <(grep -EnI "$ASSIGN" "$f" 2>/dev/null | grep -Eiv "$IGNORE")
done

echo
if [ "$fail" -ne 0 ]; then
  printf '%b\n' '\342\234\227 SECURITY CHECK FAILED - DO NOT PUSH. Fix the findings above (see STRICT-INSTRUCTIONS.md).'
  exit 1
fi
printf '%b\n' '\342\234\223 Security check passed - safe to push.'
