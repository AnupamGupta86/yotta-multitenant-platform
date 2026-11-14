# Local deployment

## Prepare the local cluster

1. Install Docker Desktop, Kind, kubectl, Helm 3.17+, GitHub CLI, and Python 3.12+ on the Mac. Start Docker Desktop. This setup binds ingress to `127.0.0.1:8081` and `127.0.0.1:8443` to avoid clashing with other local clusters.
2. Run `./scripts/setup-kind.sh` from the repository root. It builds local images and installs all Kubernetes workloads and configuration through Helm releases. `kubectl` is used only for readiness and inspection.
3. Check `kubectl get nodes` and `helm list -A`. Grafana is at `http://localhost:3000` after port-forwarding; the demo login is in the README.
4. Register the Mac as a GitHub self-hosted runner labelled `yotta-local-k8s`. Keep Docker Desktop and the runner active while the deployment workflow runs.

## Deploy tenants

On push to `master`, GitHub Actions validates the website image and publishes it to GHCR. Then the Mac runner pulls the website and event-consumer images and sequentially installs/upgrades `user1`, `user2`, and `user3` releases using their matching `.localtest.me` hosts.

For one tenant, use the **Local Kind deployment** workflow's manual dispatch and select a tenant. To use a custom domain, supply the domain input and point local DNS/hosts to the Mac's ingress address. On a public cluster, use a public DNS record and replace the self-signed ClusterIssuer with a configured ACME issuer before using real domains.

For direct CLI deployment, authenticate Docker to GHCR, then run `IMAGE=ghcr.io/<owner>/website-api:<tag> EVENT_IMAGE=ghcr.io/<owner>/website-event-consumer:latest TENANTS='user1 user2 user3' ./scripts/deploy-platform.sh`. Each tenant is installed through its website Helm release, which manages its namespace resources, database, credentials, and Kafka publish test hook. The database volume is ephemeral for local assignment use; configure persistent volume claims and backups before using it for durable data.

## Scale and inspect

`kubectl get hpa -n user1 -w` shows scale changes. In a second terminal, run `python3 scripts/load_test.py --tenant user1 --seconds 180 --workers 40`; the CPU-bound `/api/v1/workload` route generates demo traffic. Use `kubectl top pods -n user1` for the metrics-server view and the `Yotta Website Platform` dashboard for request and replica charts.

Run `./scripts/verify-deployment.sh user1 user1.localtest.me` to capture resource state, TLS HTTPS checks, HPA metrics, and event consumer logs. The GitHub deployment workflow also uploads resource/event logs as a 14-day artifact.

## Service APIs

The provisioning API, database API, and website API each use a separate database. Helm creates the API databases in the `platform` release and each website database in its tenant release. The website chart injects `DB_HOST`, `DB_NAME`, `DB_USER`, and a generated password Secret instead of embedding credentials in Helm values.
