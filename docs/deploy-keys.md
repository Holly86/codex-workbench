# Repository-scoped GitHub deploy keys

Codex Workbench can generate one Ed25519 identity for one repository on `github.com`. A key is
never reused for a second GitHub slug or local repository registration. The browser receives the
public key, SHA-256 fingerprint, lifecycle status, and timestamps only.

## Storage boundary

Private material is stored below `<WORKBENCH_DATA_DIR>/security/deploy-keys`, outside
`WORKBENCH_WORKSPACE_ROOT`. In the supplied Compose profiles, `/var/lib/codex-workbench/security`
is a separate persistent bind mount supplied by the legacy-named `WORKBENCH_PASSWORD_PATH` host
variable. Consequently, the deployed path is
`/var/lib/codex-workbench/security/deploy-keys`, and the corresponding host directory contains
both the password verifier and repository-scoped deploy-key material. Treat and back up that whole
mount as credential storage. A future dedicated-mount migration must preserve this existing path;
this release deliberately performs no silent persistence migration.

Directories use mode `0700`, private keys `0600`, and public keys at most `0644`. SQLite stores an
opaque credential ID, the canonical GitHub `owner/repository` slug, the optional local repository
ID, and non-secret lifecycle metadata. It stores neither key bytes nor browser-supplied paths.

The storage root and every identity slot are checked against symlink and path traversal escapes.
The directory is ignored by Git, and the default-deny `.dockerignore` does not admit runtime data
into either image build context. Back up this directory as sensitive credential material, separate
from repository files.

## Host verification and identity selection

Git executes an argument-array-only SSH wrapper with `IdentitiesOnly=yes`, `IdentityAgent=none`,
`BatchMode=yes`, and `StrictHostKeyChecking=yes`. It selects the private-key path derived on the
server for the exact repository credential. The wrapper uses the three current `github.com` host
keys published in GitHub's official SSH key fingerprint documentation; it does not fall back to a
user SSH agent or global SSH configuration.

The release image provides a temporary NSS identity for the effective Compose `PUID:PGID`, even
when that numeric identity is absent from the image's static passwd database. This keeps
`ssh-keygen`, the OpenSSH client, Python NSS lookups, and Git-over-SSH operational for arbitrary
non-root Synology identities without changing `/etc/passwd` or weakening the read-only root
filesystem.

When GitHub rotates a host key, update `backend/src/codex_workbench/security/github_known_hosts`
from the official GitHub documentation, review the advertised fingerprints, and deploy the
reviewed application update. Never disable strict host-key checking as a workaround.

## Existing repository flow

Open the repository menu and select **SSH / Deploy Key**. Generate the public key, then add it in
GitHub under **Repository → Settings → Deploy keys → Add deploy key**. Enable GitHub's optional
write access only when pushes are intended. Workbench does not claim write access because an
`ls-remote` probe proves repository access, not the effective write policy.

After a successful access test, the UI can preview the current and exact replacement `origin` URL.
The remote changes only after the user enters the new SSH URL verbatim and confirms the action.

## Private repository before clone

Choose **Privat**, enter an `OWNER/REPO`, HTTPS GitHub URL, or GitHub SSH URL, and generate the
dedicated key. Add only the displayed public key to that GitHub repository and run the access test.
Clone remains disabled until the exact repository probe succeeds. The local target name is
validated and atomically reserved before a background job is created. Existing names return a
synchronous conflict without exposing a host path. A successful clone binds the credential to the
derived local repository ID; any failed clone or binding removes only the target reserved by that
exact attempt.

Private deploy-key clones use the dedicated bounded clone limit
`WORKBENCH_GIT_CLONE_TIMEOUT_SECONDS` (900 seconds by default), not the short local-command limit.
On timeout, Workbench terminates only the process group created for that operation, removes only
its own still-identical reservation, and leaves the unbound credential available for a retry.

If cloning fails, opening the flow again with the same normalized GitHub slug resumes the existing
unbound credential, including its verified status and unchanged public key. The user can select a
different free local name and retry without installing another GitHub deploy key. A credential
already bound to a local repository cannot be resumed or duplicated.

## Rotation and removal

Rotation creates a candidate while retaining the active key. Add the candidate public key in
GitHub, test it, then promote it. Promotion retains the previous local key as retired material until
the user explicitly removes it. Without separately authorized GitHub API access, Workbench cannot
install or remove GitHub-side deploy keys; those steps always remain manual.
