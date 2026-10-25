# Grafana with the Smart Campus datasource and dashboards baked in (dashboards as code).
# Used where bind mounts are not available, e.g. Azure Container Apps.
FROM grafana/grafana-oss:11.4.0
COPY grafana/provisioning /etc/grafana/provisioning
COPY grafana/dashboards /var/lib/grafana/dashboards
