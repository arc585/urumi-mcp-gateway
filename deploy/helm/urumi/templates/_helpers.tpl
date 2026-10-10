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

{{/* Annotations shared by every Ingress (for example the cert-manager issuer in production). */}}
{{- define "urumi.ingressAnnotations" -}}
{{- $root := . -}}
{{- with $root.Values.ingress.annotations }}
{{- toYaml . }}
{{- end }}
{{- if and $root.Values.ingress.tls.enabled $root.Values.ingress.tls.clusterIssuer }}
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
