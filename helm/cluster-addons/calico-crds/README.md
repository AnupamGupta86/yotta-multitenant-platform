# Calico CRDs

These CRD definitions are copied from Project Calico's official
`crd.projectcalico.org.v1` Helm chart, version `v3.33.0`:

`https://github.com/projectcalico/calico/tree/v3.33.0/charts/crd.projectcalico.org.v1`

They live in Helm's `crds/` directory so Helm installs them before the Tigera
Operator chart without storing the multi-megabyte schemas in the Helm release
Secret. Helm intentionally does not update or delete CRDs on chart upgrades or
uninstalls. For a Calico CRD upgrade, review the upstream version changes and
update these files before upgrading the operator.
