#!/bin/bash
# Self-termination was removed in M2b (§2b): only AWS decides how many workers run. This stub stays
# until the next image rebuild, because the pull_code.sh baked into neurolens-worker-v2 installs it on
# every start and fails if it is missing. Nothing calls it any more (UserData's drop-in clears the
# service's OnSuccess=/OnFailure=, and UserData's error trap only logs).
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) self_terminate: self-termination removed (M2b); not ending this machine" \
  | tee -a /var/log/neurolens-boot.log >&2
exit 0
