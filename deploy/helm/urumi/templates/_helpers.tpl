{{/*
Naming and labels shared by every template.
Every resource is called "<release>-<component>", for example "urumi-wordpress".
*/}}

{{- define "urumi.fullname" -}}
{{- default .Release.Name .Values.fullnameOverride | trunc 40 | trimSuffix "-" -}}
{{- end -}}

{{/* Name for one component: include "urumi.componentName" (list . "wordpress") */}}
{{- define "urumi.componentName" -}}
{{- $root := index . 0 -}}
{{- printf "%s-%s" (include "urumi.fullname" $root) (index . 1) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Labels used to *select* a component's Pods. These must never change after install. */}}
{{- define "urumi.selectorLabels" -}}
{{- $root := index . 0 -}}
app.kubernetes.io/name: urumi
app.kubernetes.io/instance: {{ $root.Release.Name }}
app.kubernetes.io/component: {{ index . 1 }}
{{- end -}}

{{/*
Full label set: selector labels plus descriptive labels. Use on objects, NOT on Pod templates:
changing a Pod template (e.g. a version label on every chart bump) restarts the Pods, including the database.
*/}}
{{- define "urumi.labels" -}}
{{ include "urumi.selectorLabels" . }}
{{- $root := index . 0 }}
app.kubernetes.io/managed-by: {{ $root.Release.Service }}
app.kubernetes.io/version: {{ $root.Chart.AppVersion | quote }}
helm.sh/chart: {{ printf "%s-%s" $root.Chart.Name $root.Chart.Version | replace "+" "_" }}
{{- end -}}

{{/* Public URL for a host, e.g. http://store.urumi.test:8880 locally or https://store.example.com in prod. */}}
{{- define "urumi.publicUrl" -}}
{{- $root := index . 0 -}}
{{- $host := index . 1 -}}
{{- $scheme := ternary "https" "http" $root.Values.ingress.tls.enabled -}}
{{- if $root.Values.ingress.publicPort -}}
{{- printf "%s://%s:%v" $scheme $host $root.Values.ingress.publicPort -}}
{{- else -}}
{{- printf "%s://%s" $scheme $host -}}
{{- end -}}
{{- end -}}

{{/* Name of the Secret holding store credentials (generated, or pre-created in production). */}}
{{- define "urumi.storeSecretName" -}}
{{- if .Values.store.existingSecret -}}
{{- .Values.store.existingSecret -}}
{{- else -}}
{{- include "urumi.componentName" (list . "store") -}}
{{- end -}}
{{- end -}}

{{/*
Return an existing Secret value (base64, as stored) so `helm upgrade` keeps generated passwords,
or a new random value on first install. Usage:
  include "urumi.secretValue" (list $existingSecretData "key" (randAlphaNum 32))
*/}}
{{- define "urumi.secretValue" -}}
{{- $data := index . 0 -}}
{{- $key := index . 1 -}}
{{- $fresh := index . 2 -}}
{{- if and $data (hasKey $data $key) -}}
{{- index $data $key -}}
{{- else -}}
{{- $fresh | b64enc -}}
{{- end -}}
{{- end -}}

{{/* Ingress TLS block, used in production with cert-manager. */}}
{{- define "urumi.ingressTls" -}}
{{- $root := index . 0 -}}
{{- $host := index . 1 -}}
{{- $component := index . 2 -}}
{{- if $root.Values.ingress.tls.enabled }}
tls:
  - hosts:
      - {{ $host | quote }}
    secretName: {{ include "urumi.componentName" (list $root (printf "%s-tls" $component)) }}
{{- end }}
{{- end -}}

{{/*
Annotations shared by every Ingress: the operator's own, the HTTPS-redirect and HSTS Middlewares when TLS
is on (templates/ingress-tls.yaml), and the cert-manager issuer where requested.
Usage: include "urumi.ingressAnnotations" (list . true (list "gateway-body-limit"))
  2nd arg: request the certificate from this Ingress; 3rd: extra Middleware component names for this Ingress.
*/}}
{{- define "urumi.ingressAnnotations" -}}
{{- $root := index . 0 -}}
{{- $withIssuer := index . 1 -}}
{{- $middlewares := list -}}
{{- if $root.Values.ingress.tls.enabled -}}
{{- $middlewares = append $middlewares "redirect-https" -}}
{{- $middlewares = append $middlewares "hsts" -}}
{{- end -}}
{{- range $extra := index . 2 -}}{{- $middlewares = append $middlewares $extra -}}{{- end -}}
{{- $refs := list -}}
{{- range $name := $middlewares -}}
{{- $refs = append $refs (printf "%s-%s@kubernetescrd" $root.Release.Namespace (include "urumi.componentName" (list $root $name))) -}}
{{- end -}}
{{- with $root.Values.ingress.annotations }}
{{- toYaml . }}
{{- end }}
{{- if $refs }}
traefik.ingress.kubernetes.io/router.middlewares: {{ join "," $refs | quote }}
{{- end }}
{{- if and $withIssuer $root.Values.ingress.tls.enabled $root.Values.ingress.tls.clusterIssuer }}
cert-manager.io/cluster-issuer: {{ $root.Values.ingress.tls.clusterIssuer | quote }}
{{- end }}
{{- end -}}

{{/* Name of the Secret holding gateway credentials (generated, or pre-created in production). */}}
{{- define "urumi.gatewaySecretName" -}}
{{- if .Values.gateway.existingSecret -}}
{{- .Values.gateway.existingSecret -}}
{{- else -}}
{{- include "urumi.componentName" (list . "gateway") -}}
{{- end -}}
{{- end -}}

{{/* Pull secrets for our own images in a private registry (e.g. GHCR). Empty locally. */}}
{{- define "urumi.imagePullSecrets" -}}
{{- with .Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/* Pod selector for one component, for NetworkPolicy "to:"/"from:" rules: include "urumi.podsOf" (list . "postgres") */}}
{{- define "urumi.podsOf" -}}
podSelector:
  matchLabels:
    {{- include "urumi.selectorLabels" . | nindent 4 }}
{{- end -}}
