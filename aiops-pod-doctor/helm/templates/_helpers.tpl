{{/* Settings shared by the always-on Deployment and the CronJob */}}
{{- define "aiops.env" -}}
- {name: WATCH_NAMESPACES, value: {{ .Values.watchNamespaces | quote }}}
- {name: IGNORE_NAMESPACES, value: {{ .Values.ignoreNamespaces | quote }}}
- {name: LOG_TAIL_LINES, value: {{ .Values.logTailLines | quote }}}
- {name: GROK_MODEL, value: {{ .Values.grok.model | quote }}}
- {name: GROK_API_URL, value: {{ .Values.grok.apiUrl | quote }}}
- {name: GROK_TIMEOUT_SECONDS, value: {{ .Values.grok.timeoutSeconds | quote }}}
# Secrets are read from the mounted files below, not from environment variables
- {name: GROK_API_KEY_FILE, value: /etc/aiops/grok/api-key}
- {name: SMTP_SECRET_DIR, value: /etc/aiops/smtp}
{{- end }}

{{- define "aiops.volumeMounts" -}}
- {name: grok-api-key, mountPath: /etc/aiops/grok, readOnly: true}
- {name: smtp, mountPath: /etc/aiops/smtp, readOnly: true}
{{- end }}

{{- define "aiops.volumes" -}}
# optional: the pod also starts without them (built-in rules / log-only reports)
- name: grok-api-key
  secret: {secretName: {{ .Values.grok.existingSecret }}, optional: true}
- name: smtp
  secret: {secretName: {{ .Values.email.existingSecret }}, optional: true}
{{- end }}

{{- define "aiops.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
capabilities: {drop: [ALL]}
{{- end }}
