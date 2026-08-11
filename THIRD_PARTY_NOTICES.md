# Third-Party Notices

Codex Workbench depends on third-party software. This file is a concise inventory, not a replacement
for the license files and notices shipped by each dependency or base image. Container SBOM
attestations are enabled in the prepared release workflow so operators can inspect the resolved
transitive packages for a particular image digest.

## OpenAI Codex

The backend image installs `@openai/codex` 0.146.0 from the npm registry and uses its generated App
Server JSON/TypeScript schemas. The package declares Apache-2.0 and its upstream source is
<https://github.com/openai/codex>. Codex is not authored or redistributed as original Workbench
source; generated artifacts remain attributable to their upstream project.

## Backend runtime dependencies

| Package | Version | Declared license |
| --- | ---: | --- |
| argon2-cffi | 25.1.0 | MIT |
| FastAPI | 0.141.1 | MIT |
| Pillow | 12.3.0 | MIT-CMU |
| Pydantic | 2.13.4 | MIT |
| pydantic-settings | 2.14.2 | MIT |
| python-multipart | 0.0.32 | Apache-2.0 |
| Starlette | 1.3.1 | BSD-3-Clause |
| Uvicorn | 0.35.0 | BSD-3-Clause |

Resolved transitive Python components are included in the image SBOM. Development-only tools are
not installed in the final backend image.

## Frontend runtime dependencies

The application directly depends on React 19.1.1 and React DOM 19.1.1, both declaring MIT. The
production image contains the compiled static application and unprivileged nginx runtime, not the
frontend development dependency tree. Build/test tools and their exact versions are recorded in
`frontend/package-lock.json`; the resolved container SBOM is authoritative for a released digest.

## Container base images and operating-system packages

The Dockerfiles use pinned version tags for official Node and Python images and the
`nginxinc/nginx-unprivileged` image. Those images contain Debian or Alpine packages with their own
licenses and notices. Release consumers should retain the upstream notice files present in the
images and use the attached SBOM to review the exact resolved packages.

## Workbench license

The original Codex Workbench source is licensed under the Apache License 2.0 (`Apache-2.0`), whose
complete terms are in `LICENSE`. That project license does not replace or relicense any third-party
component listed above; every dependency and container package retains its upstream license and
notice obligations.
