# PythonApplication Composition — local render examples

Fast offline check of the templates' Go-template syntax and branching logic, no cluster
needed. Because the Composition uses `source: Inline` (see `../build-composition.sh`),
`../composition.yaml` is directly renderable as-is — no wrapper script needed.

```shell
crossplane render xr-defaults.yaml ../composition.yaml functions.yaml -x -r       # pip/3.12/port 8080/private
crossplane render xr-poetry-custom.yaml ../composition.yaml functions.yaml -x -r  # poetry/3.13/custom port/public, exercises the packageManager branch

# Gate-pass: mock the cluster-registry ConfigMap so the composed TektonCICD child
# and secretstore-xr actually render.
crossplane render xr-defaults.yaml ../composition.yaml functions.yaml -x -r \
  -e cluster-registry-dev-ready.yaml
```

Requires Docker (`crossplane render` pulls and runs `function-go-templating` /
`function-auto-ready` in containers by default).

**If you edit `../templates/render-github-resources/*.yaml` or
`../templates/cicd-onboarding-status/*.yaml`**, run `../build-composition.sh` first to
regenerate `../composition.yaml` from them before re-running these examples.

These fixtures only exercise the Composition's template logic — they never call the
real GitHub API, and the composed `TektonCICD` child is rendered spec-only (its own
Composition doesn't run as part of this render) — nothing here proves `TektonCICD`'s
own status actually reaches this XR's proxied conditions. See
`../../tektoncicd/example/README.md` for exercising that Composition standalone, and
this repo's own top-level README for the live-verification path.
