# Security Policy

Correlis processes adversary-controlled telemetry and potentially sensitive
security evidence. Treat every parser, connector, and analyst-facing rendering
surface as hostile-input handling code.

## Reporting a vulnerability

Do not disclose exploitable vulnerabilities in a public issue. Until a dedicated
security mailbox is established, use GitHub private vulnerability reporting for
the repository.

Include:

- Affected component and version or commit.
- Reproduction steps.
- Security impact.
- Suggested mitigation, when known.

## Supported versions

The project is pre-alpha. Only the current default branch receives security
fixes.

## Non-negotiable controls

- No default production credentials.
- No secrets in source or scenario fixtures.
- Raw evidence must be treated as untrusted content.
- AI prompts must not directly consume unbounded raw telemetry.
- Analyst actions must be attributable and auditable.
