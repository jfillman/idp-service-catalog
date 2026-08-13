#!/usr/bin/env bash
set -euo pipefail

# Regenerates composition.yaml's `source: Inline` template block from
# templates/*.yaml. Run this any time you add/edit/remove a file in
# templates/ - composition.yaml is the committed, GitOps-tracked artifact;
# the templates/ files are the maintainable source.
#
# WHY Inline instead of FileSystem+a dedicated Function/ConfigMap mount (what
# this Composition used until it was found to be actively broken): Crossplane
# v2.3.4's package manager keeps ONE shared dependency-lock graph across every
# installed Function. A second `Function` object pointing at the exact same
# `spec.package` as the pre-existing `function-go-templating` (registered for
# the ai-rollout Application Composition) doesn't just mark itself unhealthy -
# it corrupts that shared graph for every OTHER Function on the cluster too.
# Proved live on kind-dev: `function-auto-ready` had no runtime Deployment at
# all (not just Healthy: False) while the duplicate `function-go-templating-
# slo` Function existed; deleting it fixed function-auto-ready within ~10s,
# Healthy: True, Deployment created. It was silently degrading the `widget-
# api` Rollout's Ready-status reporting too (real pods were fine - 4/4
# available - only Ready-detection was broken).
#
# Inline mode sidesteps the whole problem: the template content lives in the
# Composition object itself, so every Composition can safely reuse the ONE
# shared `function-go-templating` Function with no separate mount, no
# possibility of rendering the wrong templates against the wrong XR kind (the
# "shared /templates directory" collision this design originally tried to
# avoid via a dedicated mount), and no live-cluster-only ConfigMap that isn't
# GitOps-tracked.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${SCRIPT_DIR}/composition.yaml"

COMBINED="$(
  for f in "${SCRIPT_DIR}"/templates/*.yaml; do
    cat "$f"
    echo "---"
  done
)"
# template: | sits at column 10 under inline: - block content must be
# indented MORE than that, not from column 0, or YAML parses it as leaving
# the scalar (hit this live building the local render tooling).
INDENTED="$(printf '%s\n' "$COMBINED" | sed 's/^/            /; s/^ *$//')"

cat > "$OUT" <<HEADER
# GENERATED FILE - do not hand-edit the pipeline's \`input.inline.template\`
# block below. Edit templates/*.yaml instead, then run ./build-composition.sh
# to regenerate this file. See that script's own header for why this
# Composition uses \`source: Inline\` (embedding template content directly)
# rather than \`source: FileSystem\` against a shared mount - short version: a
# second Function object pointing at the same package as the already-
# installed \`function-go-templating\` corrupted Crossplane's package-manager
# dependency-lock graph for every OTHER Function on the cluster, proved live
# on kind-dev (function-auto-ready lost its runtime Deployment entirely,
# degrading the unrelated widget-api Rollout's Ready-status reporting too).
# Inline mode reuses the existing shared Function - no new registration, no
# collision, and the templates are now fully GitOps-tracked in this file
# instead of a live-cluster-only ConfigMap.
#
# Renders Sloth's (sloth.dev) \`PrometheusServiceLevel\` + a Grafana dashboard
# ConfigMap - NOT a PrometheusRule directly. Sloth's own controller
# (gitops-cluster-dev/10-crds-operators/sloth/) does the spec->PrometheusRule
# translation. See idp/docs/service-catalog-design.md Item 4's revision
# history: this Composition originally hand-rolled the multi-window-multi-
# burn-rate PromQL itself (matching a kube-slo-style article), then switched
# to wrapping Sloth - full canonical 4-window pattern for free instead of a
# hand-rolled 2-tier simplification, matching this project's "wrap, don't
# reinvent" convention used everywhere else (Argo Rollouts, ESO, component
# charts).
#
# Delimiters: << >> instead of ai-rollout's [[ ]] or Go's default {{ }}:
#   1. PromQL range-vector syntax needs literal \`[5m]\` right next to templated
#      output - \`[[ \$w ]]\` next to a literal \`[\` creates a 3-bracket run
#      that Go's template lexer misparses.
#   2. Sloth's OWN templating syntax (\`{{.window}}\`, required literally inside
#      every errorQuery/totalQuery - see templates/prometheusservicelevel.yaml)
#      must reach the rendered PrometheusServiceLevel completely untouched,
#      for Sloth's controller to substitute per-window at generation time -
#      not for this templating pass to consume. \`{{ }}\` is exactly Go's
#      default delimiter, so it has to be something else; \`<< >>\` also
#      dodges Grafana dashboard JSON's legacy \`[[var]]\` substitution syntax.
apiVersion: apiextensions.crossplane.io/v1
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
${INDENTED}
        delims:
          left: "<<"
          right: ">>"
    - step: detect-ready
      functionRef:
        name: function-auto-ready
HEADER

echo "Wrote $OUT"
