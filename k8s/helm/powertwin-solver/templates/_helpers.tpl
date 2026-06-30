{{/*
Base name of the chart.
*/}}
{{- define "solver.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified release name.
*/}}
{{- define "solver.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "solver.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "solver.labels" -}}
helm.sh/chart: {{ include "solver.chart" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: powertwin
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{- end }}

{{/*
Per-component selector labels. Call with (dict "ctx" . "component" "flask").
*/}}
{{- define "solver.selectorLabels" -}}
app.kubernetes.io/name: {{ include "solver.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Component resource name, e.g. <release>-solver-flask.
Call with (dict "ctx" . "component" "flask").
*/}}
{{- define "solver.componentName" -}}
{{- printf "%s-%s" (include "solver.fullname" .ctx) .component | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Resolve a component image. Call with (dict "ctx" . "image" .Values.flask.image).
*/}}
{{- define "solver.image" -}}
{{- $registry := .ctx.Values.global.imageRegistry -}}
{{- $repository := .image.repository -}}
{{- $tag := .image.tag | default .ctx.Values.global.imageTag | default .ctx.Chart.AppVersion -}}
{{- if and $registry (not .image.external) -}}
{{- printf "%s/%s:%s" $registry $repository $tag -}}
{{- else -}}
{{- printf "%s:%s" $repository $tag -}}
{{- end -}}
{{- end }}

{{/*
The shared Secret name holding DB creds and solver tokens.
*/}}
{{- define "solver.secretName" -}}
{{- printf "%s-secrets" (include "solver.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Ingress host: uses .Values.ingress.{flask,mss}.host override, falls back to {branch}-{service}.{domain}.
Call with (dict "ctx" . "service" "powertwin-solver-flask").
*/}}
{{- define "solver.ingressHost" -}}
{{- printf "%s-%s.%s" .ctx.Values.global.branch .service .ctx.Values.global.domain -}}
{{- end }}
