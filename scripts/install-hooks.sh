#!/usr/bin/env bash
# Install a git pre-push hook that runs the security check before every push.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
hook="$root/.git/hooks/pre-push"
cat > "$hook" <<'EOF'
#!/usr/bin/env bash
# Auto-installed: block a push if the security scan fails. See STRICT-INSTRUCTIONS.md.
exec bash "$(git rev-parse --show-toplevel)/scripts/security-check.sh"
EOF
chmod +x "$hook"
echo "OK: pre-push hook installed - scripts/security-check.sh will run before every push."
