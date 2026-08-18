# SecretStore Composition — local render example

Fast offline check of the templates' Go-template syntax, no cluster needed.
Because the Composition uses `source: Inline` (see `../build-composition.sh`),
`../composition.yaml` is directly renderable as-is — no wrapper script needed.

```shell
crossplane render xr-checkout-api.yaml ../composition.yaml functions.yaml -x -r
```

Requires Docker (`crossplane render` pulls and runs `function-go-templating` /
`function-auto-ready` in containers by default).

`xr-checkout-api.yaml` exercises `"shared"` mode (the default, `environmentSlug`
omitted) targeting kind-dev - renders an `InfisicalProject` + a Kubernetes-Auth
`ClusterSecretStore`. `xr-checkout-api-staging.yaml` exercises the per-environment
mode (`environmentSlug: staging`, `cluster: kind-prod`) added in Item 8's
multi-cluster revision - renders an `InfisicalEnvironment` + a narrowed,
Universal-Auth `ClusterSecretStore` instead, with NO `InfisicalProject` (deliberate -
per-environment XRs reference an already-existing project, never create one). Run
either the same way, swapping the XR filename.

**If you edit `../templates/*.yaml`**, run `../build-composition.sh` first to
regenerate `../composition.yaml` from them before re-running this example.

This fixture only exercises the Composition's own rendering (both resources'
field values, deterministic naming) — it doesn't exercise
`infisical-secretstore-operator`'s own reconciliation against a real Infisical
API, which only happens on a real cluster (see that operator's own README).
