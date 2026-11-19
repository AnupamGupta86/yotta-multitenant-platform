# Rollback

Three levels, from automatic to manual.

## 1. Automatic, during every tenant deployment

`scripts/deploy-tenant.sh` (used by *Website Deployment* and the local setup):

1. remembers the current Helm revision;
2. runs `helm upgrade --install … --rollback-on-failure --wait` (`--atomic` on older Helm), so a rollout that
   does not become ready in time is rolled back by Helm itself;
3. then checks the HTTPS readiness URL and runs the Helm test (which publishes `WebsiteCreated` to Kafka);
4. if any of these fail, an exit trap runs `helm rollback <release> <saved revision>`
   (or uninstalls a first-time install), and the workflow publishes `WebsiteDeploymentFailed` to Kafka
   and fails, so the run is red and the reason is in its log and artifact.

The previous pods keep serving until new ones are ready (rolling update + PodDisruptionBudget), so a bad
release never takes the site down.

**Demo it:** run *Website Deployment* with `simulate_failure=true` (see [proof-of-work.md](proof-of-work.md#3-cicd-with-rollback-and-events)).

## 2. Redeploy a known-good version through CD

Every image has an immutable tag `1.0.0-YYYYMMDDHHMMSS`, so "roll back" is "deploy the previous tag again",
with the same approval and checks as any deployment:

```sh
gh workflow run website_cd.yml --ref master -f tenant=user1 -f domain=user1.myyotta.com -f image_tag=<previous tag>
gh workflow run hosting_platform_cd.yml --ref master -f image_tag=<previous tag>
gh workflow run website_orchestrator_cd.yml --ref master -f image_tag=<previous tag>
gh workflow run platform_cd.yml --ref master -f image_tag=<previous fluentd tag>
```

Previous tags are on each CI run's summary and on the package pages in GHCR.

## 3. Manual Helm rollback

```sh
helm history user1 -n user1              # find the last good revision
helm rollback user1 <revision> -n user1 --wait
helm status user1 -n user1
```

Same for `hosting-platform`, `website-orchestrator`, `kafka` (namespace `platform`), `myyotta-postgresql` (namespace `platform-data`) and `aiops-pod-doctor` (namespace `aiops`).

## What a rollback does not undo

Database contents. All services share one database, `myyotta_platform` (release `myyotta-postgresql` in
`platform-data`); its data lives on one PersistentVolumeClaim and is kept across rollbacks and pod restarts of
any release. Rolling back a website, the API or the orchestrator never changes a row. Schema changes must
therefore stay backward compatible with the previous release of every service that uses the database
(expand first, remove later), or be reverted by a separate migration.
