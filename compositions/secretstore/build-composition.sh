#!/usr/bin/env bash
set -euo pipefail

# Regenerates composition.yaml's `source: Inline` template block from
# templates/*.yaml. Run this any time you add/edit/remove a file in
# templates/ - composition.yaml is the committed, GitOps-tracked artifact;
# the templates/ files are the maintainable source. Same mechanism/rationale
# as compositions/slo/build-composition.sh - see that script's own header for
# the full "why Inline, not FileSystem+a dedicated Function mount" story
# (a second Function object pointing at an already-installed package
# corrupted Crossplane's package-manager dependency-lock graph, proved live).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${SCRIPT_DIR}/composition.yaml"

COMBINED="$(
  for f in "${SCRIPT_DIR}"/templates/*.yaml; do
    cat "$f"
    echo "---"
  done
)"
INDENTED="$(printf '%s\n' "$COMBINED" | sed 's/^/            /; s/^ *$//')"

cat > "$OUT" <<HEADER
# GENERATED FILE - do not hand-edit the pipeline's \`input.inline.template\`
# block below. Edit templates/*.yaml instead, then run ./build-composition.sh
# to regenerate this file. See compositions/slo/build-composition.sh for why
# this catalog uses \`source: Inline\` for every Composition rather than
# \`source: FileSystem\` against a shared mount.
#
# Renders an InfisicalProject CR (reconciled by infisical-secretstore-operator,
# gitops-cluster-dev/10-crds-operators/infisical-secretstore-operator/) and an ESO
# ClusterSecretStore pointed at the credentials that operator produces -
# idp/docs/service-catalog-design.md Item 8. NOT yet wired into
# ApplicationEnvironment's auto-provisioning (create-on-first-env, reference on
# later ones) - this XRD is standalone-creatable for this first pass, same as SLO
# was before any Attached-tier auto-provisioning existed. That wiring needs the
# idempotent-git-file-write mechanism discussed for the "N sibling XRs, one shared
# resource" problem - separate follow-up, deliberately not bolted on here.
apiVersion: apiextensions.crossplane.io/v1
kind: Composition
metadata:
  name: secretstores.catalog.idp.io
spec:
  compositeTypeRef:
    apiVersion: catalog.idp.io/v1alpha1
    kind: SecretStore
  mode: Pipeline
  pipeline:
    - step: render-secretstore-resources
      functionRef:
        name: function-go-templating
      input:
        apiVersion: gotemplating.fn.crossplane.io/v1beta1
        kind: GoTemplate
        source: Inline
        inline:
          template: |
${INDENTED}
        delims:
          left: "<<"
          right: ">>"
    - step: detect-ready
      functionRef:
        name: function-auto-ready
HEADER

echo "Wrote $OUT"
