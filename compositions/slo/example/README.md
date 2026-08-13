# SLO Composition — local render examples

Fast offline check of the templates' Go-template syntax and branching logic,
no cluster needed.

```shell
./render-local.sh xr-availability.yaml   # indicator.type: availability
./render-local.sh xr-latency.yaml        # indicator.type: latency
./render-local.sh xr-invalid.yaml        # missing errorFilter - expected to fail
```

Requires Docker (`crossplane render` pulls and runs `function-go-templating` /
`function-auto-ready` in containers by default).

**Why `render-local.sh` builds a throwaway Composition instead of using
`../composition.yaml` directly**: the real Composition uses `source:
FileSystem` against a ConfigMap mounted via a `DeploymentRuntimeConfig` on the
live cluster (see `../../../gitops-cluster-dev/10-crds-operators/crossplane/
function-slo-templates.yaml`) — `crossplane render`'s Docker-based local
runner has no equivalent local mount. The script concatenates `../templates/
*.yaml` into a `source: Inline` copy of the same Composition (same delimiters)
purely for local iteration. This exercises the template *logic* (branching on
`indicator.type`, burn-window dedup, quote-escaping via `toJson`) but not the
FileSystem/ConfigMap delivery mechanism itself — that only gets exercised by
actually applying `../composition.yaml` + the generated `slo-templates`
ConfigMap to a real cluster.
