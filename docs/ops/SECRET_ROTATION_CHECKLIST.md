# Secret Rotation Checklist

This checklist documents the process only. Do not rotate secrets unless an approved change window exists.

## Pre-Rotation

- [ ] Identify secret name and owner.
- [ ] Confirm affected services.
- [ ] Confirm rollback plan.
- [ ] Confirm operator and reviewer.
- [ ] Confirm deployment window.
- [ ] Confirm current secret is not committed in Git.

## Rotate Secret In Provider

- [ ] Generate new secret value in approved secret manager or GitHub Secrets.
- [ ] Update the secret value in the deployment secret store.
- [ ] Preserve previous secret only for rollback window if policy allows.
- [ ] Record secret version or rotation ticket.

## Deploy

- [ ] Redeploy affected services.
- [ ] Confirm containers restarted with new environment.
- [ ] Confirm health checks pass.
- [ ] Confirm authenticated endpoints work if rotating JWT secret.
- [ ] Confirm exchange connectivity if rotating exchange credentials.
- [ ] Confirm alerts if rotating notification credentials.

## Post-Rotation

- [ ] Revoke old secret after verification.
- [ ] Confirm old secret no longer works where applicable.
- [ ] Update audit record.
- [ ] Close rotation ticket.
