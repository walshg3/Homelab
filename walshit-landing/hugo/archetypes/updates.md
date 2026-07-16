+++
title = "{{ replace .File.ContentBaseName "-" " " | title }}"
summary = ""
slug = "{{ .File.ContentBaseName }}"
date = {{ .Date }}
draft = true
tags = []
affected_services = []
# Optional maintenance metadata (all optional; delete if unused):
# status = "planned"        # planned | in-progress | resolved | completed | info
# severity = "low"          # low | medium | high
# starts_at = 2026-01-01T00:00:00-04:00
# ends_at = 2026-01-01T01:00:00-04:00
# expires_at = 2026-01-08T00:00:00-04:00
+++

Write the update body here.
