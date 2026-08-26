{{- define "aggrete.name" -}}{{ .Chart.Name }}{{- end -}}
{{- define "aggrete.fullname" -}}{{ printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}{{- end -}}
{{- define "aggrete.labels" -}}
app.kubernetes.io/name: {{ include "aggrete.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}
{{- define "aggrete.selector" -}}
app.kubernetes.io/name: {{ include "aggrete.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "aggrete.redisUrl" -}}
{{- if .Values.redis.enabled -}}redis://{{ include "aggrete.fullname" . }}-redis:6379/0{{- else -}}{{ .Values.redis.externalUrl }}{{- end -}}
{{- end -}}
