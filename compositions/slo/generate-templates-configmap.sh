#!/usr/bin/env bash
set -euo pipefail

# Rebuilds the "slo-templates" ConfigMap that function-go-templating-slo reads
# its per-resource template files from (mounted at /templates via the
# mount-slo-templates DeploymentRuntimeConfig in
# gitops-cluster-dev/10-crds-operators/crossplane/function-slo-templates.yaml).
#
# Deliberately its OWN Function + ConfigMap, not a reuse of the ai-rollout
# prototype's "templates" ConfigMap/mount-templates config - that mount is a
# single flat /templates directory already holding ai-rollout's own 4 files
# (rollout/service/analysistemplate/configmap.yaml), all keyed on
# spec.parameters.* fields an SLO XR doesn't have. Sharing it would render
# those files against every SLO XR too.
#
# Run this any time you add/edit/remove a file in templates/. Kubernetes
# ConfigMaps don't watch a directory on disk, so someone has to package the
# files into one - same manual step the ai-rollout prototype's own
# generate-templates-configmap.sh already has, not solved here, just not made
# worse.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

kubectl create configmap slo-templates \
  --namespace crossplane-system \
  --from-file="${SCRIPT_DIR}/templates/" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> Updated. function-go-templating-slo's runtime pod needs restarting to"
echo "    pick up the new ConfigMap contents (ConfigMap volume updates"
echo "    propagate to mounted pods eventually, but not always fast enough"
echo "    for iterative development):"
echo ""
echo "    kubectl rollout restart deployment -n crossplane-system -l pkg.crossplane.io/function=function-go-templating-slo"
echo ""
echo "    If that label selector doesn't match anything, find the actual"
echo "    deployment name with:"
echo "    kubectl get deployments -n crossplane-system | grep function-go-templating-slo"
