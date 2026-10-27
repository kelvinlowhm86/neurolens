#!/usr/bin/env bash
# Refuse to commit secrets. Checks file names and contents of what is staged (default) or of every file
# git would see (--all: tracked + untracked-but-not-ignored).
#
#   scripts/check_secrets.sh          # staged changes (used by the pre-commit hook)
#   scripts/check_secrets.sh --all    # whole working tree, before a push
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

if [[ "${1:-}" == "--all" ]]; then
  mapfile -t files < <(git ls-files --cached --others --exclude-standard)
else
  mapfile -t files < <(git diff --cached --name-only --diff-filter=ACMR)
fi

# Files that hold real values and must only ever exist locally.
blocked_names='(^|/)(\.env|\.env\.deploy|config\.json)$|(^|/)\.env\.(local|prod|production|dev)$'

# Live-looking credentials: OpenAI / Anthropic keys, AWS access keys, Hugging Face tokens,
# Stripe secret keys and webhook secrets, private keys.
secret_re='(sk-(proj-|ant-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|hf_[A-Za-z0-9]{30,}|sk_(live|test)_[A-Za-z0-9]{16,}|rk_(live|test)_[A-Za-z0-9]{16,}|whsec_[A-Za-z0-9]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)'

problems=0
for f in "${files[@]}"; do
  [[ -f "$f" ]] || continue
  if [[ "$f" =~ $blocked_names ]]; then
    echo "BLOCKED: $f must never be committed (it holds local secrets)."
    problems=1
    continue
  fi
  # Skip binaries (images, PDFs, video).
  if ! grep -Iq . "$f" 2>/dev/null; then continue; fi
  if hits=$(grep -nE "$secret_re" "$f" 2>/dev/null); then
    echo "SECRET-LIKE VALUE in $f:"
    echo "$hits" | sed -E 's/(sk-|AKIA|ASIA|hf_|sk_|rk_|whsec_)[A-Za-z0-9_-]{4}[A-Za-z0-9_-]*/\1****/g' | head -5
    problems=1
  fi
done

if (( problems )); then
  echo
  echo "Commit refused. Move real values to .env (local) or .env.deploy → Secrets Manager (AWS)."
  exit 1
fi
echo "check_secrets: no secrets found in ${#files[@]} file(s)."
