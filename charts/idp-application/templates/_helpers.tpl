{{/*
idp-application.namespace - every resource this chart renders lives in exactly one
namespace: this release's own. Unlike platform-cicd-app (one chart, many possible
env namespaces via envNamespace), idp-application IS one env - §3's own header:
"one values.yaml, one Application, one namespace" - so there's nothing to compute.
Exists as a named template anyway so every template calls the same thing rather
than sprinkling .Release.Namespace directly, matching the sibling-chart idiom.
*/}}
{{- define "idp-application.namespace" -}}
{{- .Release.Namespace -}}
{{- end -}}

{{/*
idp-application.labels - standard Kubernetes-recommended labels (interop, matching
every other chart in this platform - see platform-cicd's docs/naming-conventions.md)
plus this platform's own platform.io/* namespace. platform.io/app is applied to
EVERY resource this chart renders, not just the Attached-tier XRs §3 explicitly
calls out ("stamps ... platform.io/app: <app> onto every XR/resource it renders
from a components:/slos: block") - naming-conventions.md's own reasoning for
platform-cicd-app ("applied to every app-chart resource, not just the
shared-namespace ones, for consistency") applies identically here, so this chart
follows the platform-wide norm rather than only the letter of §3's Attached-tier
sentence. platform.io/env and platform.io/cluster are new to this chart (not in
naming-conventions.md yet) - real, chart-scoped values unique to idp-application's
1-release-per-(app,cluster,env) shape, same audit-selector value as platform.io/app.
*/}}
{{- define "idp-application.labels" -}}
app.kubernetes.io/name: {{ .Values.appName }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- if eq (include "idp-application.hasRollout" .) "true" }}
app.kubernetes.io/version: {{ .Values.rollout.image.tag | quote }}
{{- else }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: idp
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
platform.io/component: application
platform.io/app: {{ .Values.appName }}
platform.io/env: {{ .Values.envName }}
platform.io/cluster: {{ .Values.cluster }}
{{- end -}}

{{/*
idp-application.selectorLabels - the stable subset used for Rollout matchLabels /
pod template labels / Service selector. Deliberately excludes version/chart-version
(both of which change on every deploy) - a selector must never depend on a label
that changes without changing what's being selected.
*/}}
{{- define "idp-application.selectorLabels" -}}
app.kubernetes.io/name: {{ .Values.appName }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
idp-application.hasRollout - "true"/"" - whether this release has a workload at
all. §3's "Open design question", implemented here per the doc's own stated
leaning ("optional in the same chart... for mechanism reuse") - an appType: infra
release that's only a components: block (§ Item 7's standalone-Redis case) sets
rollout: null (or omits it) and gets no Rollout/Service/HPA/AnalysisTemplate.
*/}}
{{- define "idp-application.hasRollout" -}}
{{- if .Values.rollout -}}true{{- end -}}
{{- end -}}

{{/*
idp-application.environmentRef - the deterministic <app>-<cluster>-<env> name §3's
"Linking mechanism" section defines: what every Attached-tier XR's
spec.environmentRef.name is stamped with, naming the specific ApplicationEnvironment
XR that resource depends on. See values.yaml's own header comment on cluster/env for
why those two are plain top-level fields here (not in §3's schema block verbatim).
*/}}
{{- define "idp-application.environmentRef" -}}
{{- .Values.appName }}-{{ .Values.cluster }}-{{ .Values.envName -}}
{{- end -}}

{{/*
idp-application.componentKind - looks up the Crossplane kind for one components:/
slos: entry's `type`, failing fast (not silently skipping) on an unknown type -
matching platform-cicd's validateFlows instinct of a fast, readable rejection over
a late, opaque one. `slo` is hardcoded (not in values.yaml's componentKinds map)
since the SLO XRD isn't a "component" in §3's `components:` list at all - it's its
own top-level `slos:` field with its own XRD, kept as a separate lookup entry point
so components.yaml and slos.yaml can share this same helper without slos.yaml
needing a fake "type: slo" entry.

Usage: {{ include "idp-application.componentKind" (dict "ctx" $ "type" $entry.type) }}
*/}}
{{- define "idp-application.componentKind" -}}
{{- $ctx := .ctx -}}
{{- $type := .type -}}
{{- if eq $type "slo" -}}
SLO
{{- else if hasKey $ctx.Values.componentKinds $type -}}
{{- get $ctx.Values.componentKinds $type -}}
{{- else -}}
{{- fail (printf "components: entry has unknown type '%s' - must be one of: %s (see values.yaml's componentKinds map)" $type (join ", " (keys $ctx.Values.componentKinds))) -}}
{{- end -}}
{{- end -}}

{{/*
idp-application.serviceAccountName - the name every pod this chart renders
(Rollout, Job, CronJob) sets as its own serviceAccountName, whether this chart
creates that ServiceAccount itself (serviceAccount.create: true, the default) or
one is expected to already exist (create: false - some cluster-admin process
provisioned it, e.g. with a cloud workload-identity annotation this chart doesn't
know about). Computed identically regardless of which case applies - same
"computed the same way in both places, not passed as a value" idiom already used
for the ClusterSecretStore name in external-secret.yaml's own header comment.
*/}}
{{- define "idp-application.serviceAccountName" -}}
{{- .Values.serviceAccount.name | default .Values.appName -}}
{{- end -}}

{{/*
idp-application.intOr - like Sprig's `default`, but correct for an int field where
0 is a legitimate explicit value, not "unset" - Sprig's own `default` can't tell
those apart (it treats the Go zero value as empty, full stop), which is a real bug
caught live during this build: `.backoffLimit | default 3` silently turned an
explicit `backoffLimit: 0` (a real, meaningful Kubernetes setting - "no retries")
into 3. Uses `hasKey` on the entry's own raw map instead, which does distinguish
"key present with value 0" from "key absent". SCALAR OUTPUT POSITIONS ONLY (e.g.
`backoffLimit: {{ include ... }}`) - do NOT use this inside an `{{- if }}` the way
you might for a bool: `include` always returns a string, and a non-empty string
like "false" is truthy to Go's `if`, which is the exact same class of bug relocated
one level up. A boolean "present but false vs. absent" check (see job.yaml's own
`hook:` field) needs a direct `{{- if or (not (hasKey . "hook")) .hook }}` instead,
using the real, un-stringified value.

Usage: {{ include "idp-application.intOr" (dict "map" . "key" "backoffLimit" "default" 3) }}
*/}}
{{- define "idp-application.intOr" -}}
{{- if hasKey .map .key -}}{{- get .map .key -}}{{- else -}}{{- .default -}}{{- end -}}
{{- end -}}

{{/*
idp-application.resolveAs - validates and returns a configMaps:/secrets: entry's
`as` field (env | volume | both), applying the given default when unset. Fails
fast on a typo (e.g. `as: enviroment`) rather than silently treating it as
neither - without this, a mistyped `as` would match none of the `eq` checks in
every consumption helper below and silently produce no env var, no mount, AND no
error - a config source just quietly disappears.

Usage: {{ $as := include "idp-application.resolveAs" (dict "entry" . "default" "volume") }}
*/}}
{{- define "idp-application.resolveAs" -}}
{{- $as := .entry.as | default .default -}}
{{- if not (has $as (list "env" "volume" "both")) -}}
{{- fail (printf "'%s' has invalid as: '%s' - must be one of: env, volume, both" .entry.name $as) -}}
{{- end -}}
{{- $as -}}
{{- end -}}

{{/*
idp-application.configMapObjectName - resolves which ConfigMap object name a
configMaps: entry actually refers to: `.name` when this chart renders it itself
(data: set), or `.existingConfigMap` when it doesn't (referencing one created
elsewhere - e.g. a Kustomize configMapGenerator with disableNameSuffixHash: true,
so the name is fixed and known up front, not hash-suffixed - a hash-suffixed name
would need an external process keeping this field in sync on every regeneration,
not solved here). Fails fast if an entry sets both or neither - exactly one of
data:/existingConfigMap: must be present, same fail-fast instinct as
idp-application.componentKind/batchContainer's own image-fallback check.
*/}}
{{- define "idp-application.configMapObjectName" -}}
{{- if and (hasKey . "data") (hasKey . "existingConfigMap") -}}
{{- fail (printf "configMaps: entry '%s' sets both data: and existingConfigMap: - set exactly one (data: for a ConfigMap this chart renders, existingConfigMap: to reference one created elsewhere)" .name) -}}
{{- else if hasKey . "existingConfigMap" -}}
{{- .existingConfigMap -}}
{{- else if hasKey . "data" -}}
{{- .name -}}
{{- else -}}
{{- fail (printf "configMaps: entry '%s' needs either data: (this chart renders the ConfigMap) or existingConfigMap: (referencing one created elsewhere)" .name) -}}
{{- end -}}
{{- end -}}

{{/*
idp-application.workloadEnv - the container env list shared by every workload
(main Rollout container, every jobs:/cronJobs: entry): .Values.env verbatim, plus
one secretKeyRef entry per .Values.secrets entry whose `as` (default: env)
includes env (see external-secret.yaml - same `app-secrets` target Secret, `key`
field means the container-facing env var name here). Factored out so Job/CronJob
don't re-derive this - a batch task in the same app almost always wants the same
config/secrets the main workload has, and this is the one place that mapping is
defined.

Usage: {{ $env := fromYamlArray (include "idp-application.workloadEnv" $) }}
*/}}
{{- define "idp-application.workloadEnv" -}}
{{- $env := list -}}
{{- range .Values.env -}}
{{- $env = append $env (dict "name" .name "value" (.value | toString)) -}}
{{- end -}}
{{- range .Values.secrets -}}
{{- $as := include "idp-application.resolveAs" (dict "entry" . "default" "env") -}}
{{- if or (eq $as "env") (eq $as "both") -}}
{{- $envName := .key | default (upper (regexReplaceAll "-" .name "_")) -}}
{{- $env = append $env (dict "name" $envName "valueFrom" (dict "secretKeyRef" (dict "name" "app-secrets" "key" .name))) -}}
{{- end -}}
{{- end -}}
{{- $env | toYaml -}}
{{- end -}}

{{/*
idp-application.workloadEnvFrom - one envFrom: configMapRef entry per configMaps:
entry whose `as` (default: volume) includes env - every key in that ConfigMap's
data becomes an env var verbatim, using the keys' own names (no per-key renaming -
use the plain top-level env: list for a single renamed value instead). Separate
template from workloadEnv since envFrom is its own container-spec field, not
merged into the individual-entry env: list.

Usage: {{ $envFrom := fromYamlArray (include "idp-application.workloadEnvFrom" $) }}
*/}}
{{- define "idp-application.workloadEnvFrom" -}}
{{- $envFrom := list -}}
{{- range .Values.configMaps -}}
{{- $as := include "idp-application.resolveAs" (dict "entry" . "default" "volume") -}}
{{- if or (eq $as "env") (eq $as "both") -}}
{{- $envFrom = append $envFrom (dict "configMapRef" (dict "name" (include "idp-application.configMapObjectName" .))) -}}
{{- end -}}
{{- end -}}
{{- $envFrom | toYaml -}}
{{- end -}}

{{/*
idp-application.workloadVolumeMounts / idp-application.workloadVolumes - the
volumeMount/volume pair for every configMaps: entry whose `as` (default: volume)
includes volume, every volumes: (PVC) entry, and every secrets: entry whose `as`
(default: env) includes volume - shared by every workload the same way workloadEnv
is. Two separate templates (mount goes on the container, volume goes on the pod)
computed from the same three values lists, so a Job/CronJob container and pod spec
both call in and stay consistent by construction rather than by two templates
being kept in sync by hand.

secrets: volume entries deliberately do NOT get their own Secret/volume each -
every `as: volume`/`both` entry shares the single `app-secrets` Secret volume
(added once, only if at least one entry needs it), each mounted at its own exact
file path via `subPath: <name>` - the standard mechanism for projecting one key
from a Secret to an exact path without a volume per key. This is also why a
secrets: entry's mountPath means "the exact file", unlike configMaps'/volumes'
mountPath, which is a directory a ConfigMap's/PVC's whole content mounts under.

Usage: {{ $volumeMounts := fromYamlArray (include "idp-application.workloadVolumeMounts" $) }}
      {{ $volumes := fromYamlArray (include "idp-application.workloadVolumes" $) }}
*/}}
{{- define "idp-application.workloadVolumeMounts" -}}
{{- $volumeMounts := list -}}
{{- range .Values.configMaps -}}
{{- $as := include "idp-application.resolveAs" (dict "entry" . "default" "volume") -}}
{{- if or (eq $as "volume") (eq $as "both") -}}
{{- $mountPath := .mountPath | default (printf "/config/%s" .name) -}}
{{- $volumeMounts = append $volumeMounts (dict "name" .name "mountPath" $mountPath) -}}
{{- end -}}
{{- end -}}
{{- range .Values.volumes -}}
{{- $volumeMounts = append $volumeMounts (dict "name" .name "mountPath" .mountPath) -}}
{{- end -}}
{{- range .Values.secrets -}}
{{- $as := include "idp-application.resolveAs" (dict "entry" . "default" "env") -}}
{{- if or (eq $as "volume") (eq $as "both") -}}
{{- $mountPath := .mountPath | default (printf "/secrets/%s" .name) -}}
{{- $volumeMounts = append $volumeMounts (dict "name" "app-secrets" "mountPath" $mountPath "subPath" .name "readOnly" true) -}}
{{- end -}}
{{- end -}}
{{- $volumeMounts | toYaml -}}
{{- end -}}

{{- define "idp-application.workloadVolumes" -}}
{{- $volumes := list -}}
{{- range .Values.configMaps -}}
{{- $as := include "idp-application.resolveAs" (dict "entry" . "default" "volume") -}}
{{- if or (eq $as "volume") (eq $as "both") -}}
{{- $volumes = append $volumes (dict "name" .name "configMap" (dict "name" (include "idp-application.configMapObjectName" .))) -}}
{{- end -}}
{{- end -}}
{{- range .Values.volumes -}}
{{- $volumes = append $volumes (dict "name" .name "persistentVolumeClaim" (dict "claimName" .name)) -}}
{{- end -}}
{{- $needsSecretVolume := false -}}
{{- range .Values.secrets -}}
{{- $as := include "idp-application.resolveAs" (dict "entry" . "default" "env") -}}
{{- if or (eq $as "volume") (eq $as "both") -}}{{- $needsSecretVolume = true -}}{{- end -}}
{{- end -}}
{{- if $needsSecretVolume -}}
{{- $volumes = append $volumes (dict "name" "app-secrets" "secret" (dict "secretName" "app-secrets")) -}}
{{- end -}}
{{- $volumes | toYaml -}}
{{- end -}}

{{/*
idp-application.batchContainer - builds the single container spec shared by every
jobs:/cronJobs: entry (job.yaml/cronjob.yaml). Falls back to rollout.image when the
entry doesn't set its own `image:` - fails fast (not a silent empty image string)
when neither is available, i.e. rollout: isn't set at all and the entry didn't
supply one either.

Usage: {{ $container := fromYaml (include "idp-application.batchContainer" (dict "ctx" $ "entry" .)) }}
*/}}
{{- define "idp-application.batchContainer" -}}
{{- $ctx := .ctx -}}
{{- $entry := .entry -}}
{{- $image := $entry.image -}}
{{- if not $image -}}
{{- if $ctx.Values.rollout -}}{{- $image = $ctx.Values.rollout.image -}}{{- end -}}
{{- end -}}
{{- if or (not $image) (not $image.repository) -}}
{{- fail (printf "'%s' needs an image - rollout: is not set (or has no image), so there's no default to fall back to. Set this entry's own image: {repository, tag}." $entry.name) -}}
{{- end -}}
{{- $container := dict "name" $entry.name "image" (printf "%s:%s" $image.repository $image.tag) -}}
{{- if $entry.command -}}{{- $_ := set $container "command" $entry.command -}}{{- end -}}
{{- if $entry.args -}}{{- $_ := set $container "args" $entry.args -}}{{- end -}}
{{- if $entry.resources -}}{{- $_ := set $container "resources" $entry.resources -}}{{- end -}}
{{- if $entry.containerSecurityContext -}}{{- $_ := set $container "securityContext" $entry.containerSecurityContext -}}{{- end -}}
{{- $env := fromYamlArray (include "idp-application.workloadEnv" $ctx) -}}
{{- if $env -}}{{- $_ := set $container "env" $env -}}{{- end -}}
{{- $envFrom := fromYamlArray (include "idp-application.workloadEnvFrom" $ctx) -}}
{{- if $envFrom -}}{{- $_ := set $container "envFrom" $envFrom -}}{{- end -}}
{{- $volumeMounts := fromYamlArray (include "idp-application.workloadVolumeMounts" $ctx) -}}
{{- if $volumeMounts -}}{{- $_ := set $container "volumeMounts" $volumeMounts -}}{{- end -}}
{{- $container | toYaml -}}
{{- end -}}
