# TektonCICD Composition — local render examples

Fast offline check of the templates' Go-template syntax and branching logic, no cluster
needed. Because the Composition uses `source: Inline` (see `../build-composition.sh`),
`../composition.yaml` is directly renderable as-is — no wrapper script needed.

```shell
# Gate fails: no cluster-registry ConfigMap for kind-dev in this render's world, so
# render-tektoncicd-resources renders nothing and status reports DevClusterReady:
# False / CicdOnboarded: False, while this XR's own Ready stays vacuously True
# (nothing composed to wait on).
crossplane render xr-widget-service.yaml ../composition.yaml functions.yaml -x -r

# Gate passes: mock the registry ConfigMap via --required-resources, exercising the
# cicd-identity-yaml RepositoryFile render and both status conditions' True branch.
crossplane render xr-widget-service.yaml ../composition.yaml functions.yaml -x -r \
  -e cluster-registry-dev-ready.yaml
```

Requires Docker (`crossplane render` pulls and runs `function-go-templating` /
`function-auto-ready` in containers by default).

**If you edit `../templates/render-tektoncicd-resources/*.yaml` or
`../templates/tektoncicd-status/*.yaml`**, run `../build-composition.sh` first to
regenerate `../composition.yaml` from them before re-running these examples — the
templates are the maintainable source, `composition.yaml` is generated and
GitOps-tracked.

These fixtures only exercise the Composition's template logic — they never call the
real GitHub API. `crossplane render` treats every `provider-github` managed resource
as spec-only output; nothing here proves the resources actually reconcile against
GitHub. That needs a real cluster with `provider-github` installed and a real
credential — see `idp/docs/service-catalog-design.md` §1 and this repo's own
top-level README for the live-verification path.
