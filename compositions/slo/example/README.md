# SLO Composition — local render examples

Fast offline check of the templates' Go-template syntax and branching logic,
no cluster needed. Because the Composition uses `source: Inline` (see
`../build-composition.sh`), `../composition.yaml` is directly renderable as-is
— no wrapper script needed.

```shell
crossplane render xr-availability.yaml ../composition.yaml functions.yaml -x -r   # indicator.type: availability
crossplane render xr-latency.yaml ../composition.yaml functions.yaml -x -r        # indicator.type: latency
crossplane render xr-invalid.yaml ../composition.yaml functions.yaml -x -r        # missing errorFilter - expected to fail
```

Requires Docker (`crossplane render` pulls and runs `function-go-templating` /
`function-auto-ready` in containers by default).

**If you edit `../templates/*.yaml`**, run `../build-composition.sh` first to
regenerate `../composition.yaml` from them before re-running these examples —
the templates are the maintainable source, `composition.yaml` is generated
and GitOps-tracked.

These fixtures only exercise the Composition's template logic (branching on
`indicator.type`, quote-escaping via `toJson`) — they don't exercise Sloth's
own `PrometheusServiceLevel` → `PrometheusRule` translation, which only
happens on a real cluster with `sloth-operator` running
(`gitops-cluster-dev/10-crds-operators/sloth/`).
