# ApplicationEnvironment Composition — local render examples

Fast offline check of the templates' Go-template syntax and branching logic, no
cluster needed. Because the Composition uses `source: Inline` (see
`../build-composition.sh`), `../composition.yaml` is directly renderable as-is — no
wrapper script needed.

```shell
crossplane render xr-dev.yaml ../composition.yaml functions.yaml -x -r
```

Requires Docker (`crossplane render` pulls and runs `function-go-templating` /
`function-auto-ready` in containers by default).

**If you edit `../templates/render-github-resources/*.yaml` or
`../templates/workload-status/*.yaml`**, run `../build-composition.sh` first to
regenerate `../composition.yaml` from them before re-running this example — the
templates are the maintainable source, `composition.yaml` is generated and
GitOps-tracked.

Just one fixture, deliberately — `crossplane render` skips real API-server admission,
so it can't actually prove the `env` enum rejects a typo (same honest caveat
`../../nodejsapplication/example/README.md` already states for its own schema-level
defaults); no fabricated "invalid" fixture that couldn't prove what it claims to.

This fixture only exercises the Composition's template logic (rendered
`RepositoryFile` shapes, the `WorkloadDeployed: False` status patch) — it never calls
the real GitHub API. `crossplane render` treats every `provider-github` managed
resource as spec-only output; nothing here proves the resources actually reconcile
against GitHub, or that ArgoCD's `tenant-onboarding` ApplicationSet actually picks up
the committed `identity.yaml`. That needs a real cluster with `provider-github`
installed and a real credential — see `idp/docs/service-catalog-design.md` Item 3 and
this repo's own top-level README for the live-verification path.
