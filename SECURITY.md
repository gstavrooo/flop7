# Security Policy

## Supported versions

Flop7 is a non-commercial hobby project. Only the current `main` and the latest
tag receive fixes. Old tags and the paused Render snapshot are unsupported.

| Version | Supported |
| --- | --- |
| `main` / latest tag | yes |
| older tags / Render snapshot | no |

## Reporting a vulnerability

Please **do not** open a public issue for a security problem. Use GitHub's
private vulnerability reporting:
[Report a vulnerability](https://github.com/gstavrooo/flop7/security/advisories/new).

Include the affected URL or commit, reproduction steps and the impact you see.
This is a best-effort project maintained in spare time, so response times are
not guaranteed.

## Threat model

- Games have no accounts. Each seat is authorised by a secret token returned
  once at creation and sent in the `X-Seat-Token` header; a game id alone only
  reveals that game's public view.
- Invite tokens travel in the URL fragment and are stripped from the address
  bar. They are never sent to the server or written to server logs.
- Rate limits on game creation and SSE concurrency are in-process abuse guards,
  not quotas: they are per worker and reset on restart.
- The service is public and unauthenticated by design. Please do not put
  sensitive data into player names.

## Scope

Deployment secrets live in GitHub Actions secrets and never in the repository;
gitleaks scans the full history on every pull request. Reports about the hosting
provider or the original card game are out of scope.
