# Generated Codex app-server schemas

These artifacts were generated from the locally installed OpenAI Codex CLI 0.146.0.

Generator commands:

    codex app-server generate-ts --out schemas/codex-app-server/typescript
    codex app-server generate-json-schema --out schemas/codex-app-server/json

Do not edit files below typescript/ or json/ manually. Update VERSION only as an intentional
Codex upgrade, install that exact CLI version, run scripts/update-app-server-schemas.sh, and review
the generated protocol diff together with the integration code and ADR 0001.

The generators were run without --experimental. The output still includes schema declarations for
the version's protocol surface; runtime use of gated fields and methods requires explicit
experimentalApi negotiation and is outside the foundation phase.
