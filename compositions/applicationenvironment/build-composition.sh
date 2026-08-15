#!/usr/bin/env bash
set -euo pipefail

# Regenerates composition.yaml's two `source: Inline` template blocks from
# templates/render-github-resources/*.yaml and templates/workload-status/*.yaml. Run
# this any time you add/edit/remove a file in either directory - composition.yaml is
# the committed, GitOps-tracked artifact; the templates/ files are the maintainable
# source. Same split-by-pipeline-step generation approach as
# compositions/nodejsapplication/build-composition.sh, copied directly (two
# function-go-templating steps, not one - one composes the real provider-github
# resources, the other patches the XR's own status afterward).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${SCRIPT_DIR}/composition.yaml"

render_dir() {
  local dir="$1"
  local combined
  combined="$(
    for f in "${dir}"/*.yaml; do
      cat "$f"
      echo "---"
    done
  )"
  # template: | sits at column 10 under inline: - block content must be indented MORE
  # than that, not from column 0, or YAML parses it as leaving the scalar (same gotcha
  # documented in compositions/nodejsapplication/build-composition.sh).
  printf '%s\n' "$combined" | sed 's/^/            /; s/^ *$//'
}

GITHUB_RESOURCES="$(render_dir "${SCRIPT_DIR}/templates/render-github-resources")"
WORKLOAD_STATUS="$(render_dir "${SCRIPT_DIR}/templates/workload-status")"

cat > "$OUT" <<HEADER
# GENERATED FILE - do not hand-edit the pipeline's \`input.inline.template\` blocks
# below. Edit templates/render-github-resources/*.yaml or
# templates/workload-status/*.yaml instead, then run ./build-composition.sh to
# regenerate this file.
#
# Two function-go-templating steps, not one (compositions/nodejsapplication's own
# pattern, copied directly): render-github-resources composes the real
# provider-github managed resources (RepositoryFile - see
# idp/docs/service-catalog-design.md Item 3), workload-status patches the XR's own
# status afterward with a WorkloadDeployed: False custom condition (see that
# template's own header for the mechanism and why). Both share the ONE
# already-installed function-go-templating Function via source: Inline - registering
# a second Function package pointing at the same reference corrupted Crossplane's
# shared dependency-lock graph cluster-wide, a real bug hit live building the SLO
# Composition (see compositions/slo/build-composition.sh's own header) - never
# repeat that here.
#
# Delimiters: << >> instead of Go's default {{ }}, matching every other Composition
# in this catalog - kept consistent even though this one has no PromQL/Sloth
# templating syntax of its own to dodge; \`<<setResourceNameAnnotation ...>>\` calls
# below are function-go-templating's own helper, unaffected by the delimiter choice.
apiVersion: apiextensions.crossplane.io/v1
kind: Composition
metadata:
  name: applicationenvironments.catalog.idp.io
spec:
  compositeTypeRef:
    apiVersion: catalog.idp.io/v1alpha1
    kind: ApplicationEnvironment
  mode: Pipeline
  pipeline:
    - step: render-github-resources
      functionRef:
        name: function-go-templating
      input:
        apiVersion: gotemplating.fn.crossplane.io/v1beta1
        kind: GoTemplate
        source: Inline
        inline:
          template: |
${GITHUB_RESOURCES}
        delims:
          left: "<<"
          right: ">>"
    - step: detect-ready
      functionRef:
        name: function-auto-ready
    - step: workload-status
      functionRef:
        name: function-go-templating
      input:
        apiVersion: gotemplating.fn.crossplane.io/v1beta1
        kind: GoTemplate
        source: Inline
        inline:
          template: |
${WORKLOAD_STATUS}
        delims:
          left: "<<"
          right: ">>"
HEADER

echo "Wrote $OUT"
