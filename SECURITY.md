# Security Policy

Thank you for helping keep `agentic-exception-sdk` and its users safe. This
document explains how to report a vulnerability and what to expect in return.

## Supported versions

Security fixes are provided for the latest released minor version. Older
versions may not receive patches — please upgrade to the latest release before
reporting, where practical.

| Version | Supported          |
| ------- | ------------------ |
| 1.1.x   | :white_check_mark: |
| < 1.1   | :x:                |

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues,
pull requests, or discussions.** Public disclosure before a fix is available
puts other users at risk.

Instead, report privately through GitHub's private vulnerability reporting:

1. Go to the repository's **Security** tab:
   https://github.com/29monsankye/agentic-exception-sdk/security
2. Click **Report a vulnerability**.
3. Fill in the advisory form with the details below.

This opens a private channel visible only to the maintainers.

### What to include

To help us triage quickly, please include as much of the following as you can:

- A description of the vulnerability and its impact.
- The affected version(s) and, if known, the affected module or code path.
- Steps to reproduce, or a minimal proof-of-concept.
- Any relevant configuration (e.g. which persistence provider, redaction rules,
  or optional extras were in use).
- Your assessment of severity, if you have one.

Please do **not** include real secrets, production data, or live credentials in
your report — a redacted or synthetic reproduction is preferred.

## Our commitment

This is an open-source project maintained on a best-effort basis. When you send
a report, we will make a good-faith effort to:

- **Acknowledge** that we received it.
- **Assess** it (accepted / needs-more-info / not-a-vuln) and let you know.
- **Keep you informed** of remediation progress for accepted reports.
- **Credit** you in the release notes and/or the published advisory when a fix
  ships, unless you ask to remain anonymous.

We do not commit to specific response or resolution timelines. Turnaround
depends on maintainer availability, severity, and report complexity.

## Coordinated disclosure

We follow a coordinated-disclosure model:

- We ask that you give us a reasonable opportunity to investigate and release a
  fix before any public disclosure, and we will work with you in good faith on
  disclosure timing based on severity and complexity.
- Once a fix is released, we will publish a security advisory (GitHub Security
  Advisory / GHSA) describing the issue and crediting the reporter.
- If a reported issue is already public, or is being actively exploited, we may
  act more quickly.

## Scope

This policy covers the code in this repository (the `agentic_exception_sdk`
package and its tests/benchmarks). Vulnerabilities in **third-party
dependencies** should be reported to the respective upstream projects; if a
dependency issue affects this SDK's behavior, we still welcome a heads-up so we
can pin, patch, or document it.

Before reporting, please review the **Limitations & Scope** section of the
[README](README.md). Several properties are documented, intentional trade-offs
rather than vulnerabilities — for example:

- **Redaction is best-effort and pattern-based.** Novel, split, or obfuscated
  secrets can pass through; it is not a substitute for a dedicated DLP control.
- **`AgentHardKillError` extends `BaseException` by design.** It is in-execution
  termination, not an out-of-band operator or fleet kill switch.
- **Default integrity checkpoints are unsigned and non-durable**, and attest
  this via `sentirock.attestation()` (`durable=false`).

A report that demonstrates behavior *worse* than what the README documents
(e.g. a redaction bypass under a configuration the docs claim is covered, an
integrity check that can be silently forged, or a way to suppress a `HARD_KILL`
that should fire) is very much in scope and welcome.

## Safe harbor

We will not pursue or support legal action against researchers who:

- Make a good-faith effort to comply with this policy,
- Avoid privacy violations, data destruction, and service degradation, and
- Report promptly and do not exploit the issue beyond what is necessary to
  demonstrate it.

Thank you for practicing responsible disclosure.
