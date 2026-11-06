"""Every action a Terraform role policy grants must also be allowed by the permissions boundary
(infra/iam/neurolens-role-boundary.json), or AWS silently refuses it at run time. Trust
policies' sts:AssumeRole is not subject to the boundary. Resources are not compared."""

import fnmatch
import json
import re
from pathlib import Path

INFRA = Path(__file__).resolve().parent.parent / "infra"


def test_every_role_action_fits_under_the_boundary():
    statements = json.loads((INFRA / "iam" / "neurolens-role-boundary.json").read_text())
    allowed = []
    for st in statements["Statement"]:
        allowed += [st["Action"]] if isinstance(st["Action"], str) else st["Action"]
    missing = []
    for tf in sorted((INFRA / "terraform").glob("*.tf")):
        for m in re.finditer(r'\bAction\s*=\s*(\[[^\]]*\]|"[^"]*")', tf.read_text()):
            for action in re.findall(r'"([^"]+)"', m.group(1)):
                if action == "sts:AssumeRole":
                    continue
                if not any(fnmatch.fnmatchcase(action, p) for p in allowed):
                    missing.append(f"{tf.name}: {action}")
    assert not missing, missing
