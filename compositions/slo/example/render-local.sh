#!/usr/bin/env bash
set -euo pipefail

# Fast offline check of the SLO templates' Go-template syntax/logic, no
# cluster needed. The real Composition (../composition.yaml) uses
# `source: FileSystem` against a ConfigMap mounted via a DeploymentRuntimeConfig
# on the live cluster - `crossplane render`'s Docker-based local runner has no
# equivalent local mount, so this script builds a throwaway `source: Inline`
# copy of the same Composition (templates concatenated, same delimiters) purely
# for local iteration. It exercises the template LOGIC (branching, burn-window
# dedup, quote-escaping) but not the FileSystem/ConfigMap delivery mechanism
# itself - that only gets exercised by actually applying to a cluster.
#
# Usage: ./render-local.sh xr-availability.yaml
#        ./render-local.sh xr-latency.yaml
#        ./render-local.sh xr-invalid.yaml   # exercises the fail() guard - expected to error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMP_DIR="$(dirname "$SCRIPT_DIR")"
XR="${1:?usage: render-local.sh <xr-fixture.yaml>}"
WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

{
  for f in "$COMP_DIR"/templates/*.yaml; do
    cat "$f"
    echo "---"
  done
} > "$WORKDIR/combined.yaml"

python3 - "$WORKDIR/combined.yaml" "$WORKDIR/composition-inline.yaml" <<'PYEOF'
import sys
combined_path, out_path = sys.argv[1], sys.argv[2]
with open(combined_path) as f:
    content = f.read()
# template: | sits at column 10 under inline: - block content must be indented
# MORE than that, not from column 0, or YAML parses it as leaving the scalar.
indented = "\n".join(("            " + line if line.strip() else "") for line in content.splitlines())
composition = f"""apiVersion: apiextensions.crossplane.io/v1
kind: Composition
metadata:
  name: slos.catalog.idp.io
spec:
  compositeTypeRef:
    apiVersion: catalog.idp.io/v1alpha1
    kind: SLO
  mode: Pipeline
  pipeline:
    - step: render-slo-resources
      functionRef:
        name: function-go-templating
      input:
        apiVersion: gotemplating.fn.crossplane.io/v1beta1
        kind: GoTemplate
        source: Inline
        inline:
          template: |
{indented}
        delims:
          left: "<<"
          right: ">>"
    - step: detect-ready
      functionRef:
        name: function-auto-ready
"""
with open(out_path, "w") as f:
    f.write(composition)
PYEOF

crossplane render "$SCRIPT_DIR/$XR" "$WORKDIR/composition-inline.yaml" "$SCRIPT_DIR/functions.yaml" -x -r
