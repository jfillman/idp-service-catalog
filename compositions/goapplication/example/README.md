# GoApplication Composition — local render examples

Fast offline check of the templates' Go-template syntax and branching logic, no cluster
needed. Because the Composition uses `source: Inline` (see `../build-composition.sh`),
`../composition.yaml` is directly renderable as-is — no wrapper script needed.

```shell
crossplane render xr-defaults.yaml ../composition.yaml functions.yaml -x -r

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
real GitHub API, and the composed `TektonCICD` child is rendered spec-only. See
`../../tektoncicd/example/README.md` for exercising that Composition standalone, and
this repo's own top-level README for the live-verification path.
