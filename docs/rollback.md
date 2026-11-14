# Deployment rollback

Helm upgrade uses `--atomic --wait`, so Kubernetes readiness failures automatically roll back a failed upgrade. The deploy script records the last successful Helm revision before upgrade. It then tests the HTTPS readiness route and publishes the deployment event to Kafka. If either post-upgrade check fails, the exit trap runs `helm rollback` to the saved revision. If this was the first install, it uninstalls the failed release.

Inspect releases and revisions with:

```sh
helm history user1 -n user1
helm status user1 -n user1
```

To roll back manually:

```sh
helm rollback user1 <revision> -n user1 --wait
```

Rollback preserves the previous deployment/image but does not restore changes made inside the tenant database. The included local tenant databases use emptyDir and are intended for demonstration only.
