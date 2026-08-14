# NodeJSApplication Composition — local render examples

Fast offline check of the templates' Go-template syntax and branching logic, no cluster
needed. Because the Composition uses `source: Inline` (see `../build-composition.sh`),
`../composition.yaml` is directly renderable as-is — no wrapper script needed.

```shell
crossplane render xr-defaults.yaml ../composition.yaml functions.yaml -x -r      # every field defaulted (nodeVersion 20, npm, port 8080, private)
crossplane render xr-pnpm-custom.yaml ../composition.yaml functions.yaml -x -r   # pnpm/Node 22/custom port/public, exercises the packageManager branch
```

Requires Docker (`crossplane render` pulls and runs `function-go-templating` /
`function-auto-ready` in containers by default).

**If you edit `../templates/render-github-resources/*.yaml` or
`../templates/cicd-onboarding-status/*.yaml`**, run `../build-composition.sh` first to
regenerate `../composition.yaml` from them before re-running these examples — the
templates are the maintainable source, `composition.yaml` is generated and
GitOps-tracked.

These fixtures only exercise the Composition's template logic (rendered `Repository`/
`RepositoryFile` shapes, the `packageManager` branch, the forced `Ready: False` status
patch) — they never call the real GitHub API. `crossplane render` treats every
`provider-github` managed resource as spec-only output; nothing here proves the
resources actually reconcile against GitHub. That needs a real cluster with
`provider-github` installed and a real credential — see
`idp/docs/service-catalog-design.md` §1 and this repo's own top-level README for the
live-verification path.
