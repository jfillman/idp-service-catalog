# idp-service-catalog

Crossplane XRDs, Compositions, Composition Functions, and the
`idp-application` Helm chart for [Dream IDP](https://github.com/jfillman/idp)
— the service catalog a Backstage-driven Crossplane plugin turns into
self-service templates. Design lives in `idp`'s
[`docs/service-catalog-design.md`](https://github.com/jfillman/idp/blob/main/docs/service-catalog-design.md);
this repo is where it's actually built.

## Status

**AI-triage mechanism (Phase 2, first slice) — done, live-verified
2026-08-13.** `functions/function-rollout-watcher` + `functions/diagnosis-holmes-dispatch`:
a Crossplane Composition Function that watches an Argo Rollout and, on
`Degraded`, dispatches a diagnosis request to a shared HolmesGPT service
instead of running a bespoke per-app Claude agent. Extracted and redesigned
from the [`ai-rollout`](https://github.com/jfillman/ai-rollout) prototype
(left untouched as a standalone demo). Proven end-to-end on a fresh
`kind-dev` cluster: a real broken canary → real diagnosis → real fix PR,
[jfillman/idp#8](https://github.com/jfillman/idp/pull/8). See each
function's own README for the full detail.

**Not started yet**: the rest of Phase 2 — generalizing this mechanism
beyond the single-repo `widget-api` demo shape, the `idp-application` Helm
chart itself (`charts/idp-application`, per `service-catalog-design.md` §3),
and the v1 XRD catalog (`NodeJSApplication`, `SpringBootApplication`,
`ApplicationEnvironment`, `SLO`, and the Component XRDs).

## Layout (so far)

```
functions/
  function-rollout-watcher/    Composition Function: watches Rollout, dispatches diagnosis
  diagnosis-holmes-dispatch/   Thin Job: hands the investigation off to HolmesGPT
```

`charts/`, `xrds/`, and `compositions/` don't exist yet — next up.
