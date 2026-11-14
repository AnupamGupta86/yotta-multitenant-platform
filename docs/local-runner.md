# Local GitHub Actions runner on macOS

The deployment job uses a **self-hosted Mac runner** with the labels `self-hosted`, `macOS`, and `yotta-local-k8s`. GitHub-hosted runners validate and publish the image; the Mac runner is only used for deployment into the Kind cluster running on that Mac. Intel and Apple Silicon Macs are both supported.

## Register the runner

1. In the GitHub repository, open **Settings → Actions → Runners → New self-hosted runner** and select macOS and the Mac's architecture.
2. Follow GitHub's displayed commands to download and configure the official runner. Add the custom label `yotta-local-k8s` when prompted. Keep the short-lived registration token private; do not add it to the repository or workflow.
3. Start the runner in the foreground with GitHub's `run.sh` command while deploying. For a persistent local setup, install it as a dedicated macOS launch agent using GitHub's instructions and keep the Mac awake.
4. Keep Docker Desktop running and run `./scripts/setup-kind.sh` once. The runner must use the same local account and Docker context that owns the Kind cluster.
5. From **Settings → Actions → General**, allow repository workflows to use the self-hosted runner. Review queued deployment workflows before enabling them on a shared or publicly exposed machine.

The workflow never registers a runner or stores a runner token. Self-hosted runners execute repository code on the Mac, so only use this setup for a repository whose workflow changes you trust.
