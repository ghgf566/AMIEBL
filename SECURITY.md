# AMIEBL Security Policy

## Supported versions

AMIEBL is preparing its first public `v1.0.0` release. Security fixes will be evaluated for the latest published release and the current `main` branch. Older builds and experimental branches are not guaranteed to receive backports.

## Reporting a vulnerability

Please **do not publish exploit details, credentials, API keys, administrator tokens, or private logs in a public issue**.

If the GitHub repository's **Report a vulnerability** feature is available, use it to submit a private security advisory. Otherwise, open a GitHub issue containing **only a non-sensitive request for a private reporting channel**. The maintainer will determine the appropriate next steps. No guaranteed response or remediation time is currently offered.

Describe the affected version or commit, the impact, and a minimal reproduction using only test data. Redact local paths and any sensitive information.

## Local deployment assumptions

AMIEBL is designed for a **personal Windows computer**. Its OpenAI-compatible inference API is bound to the loopback interface and is not intended to be made publicly accessible. Management endpoints require a local administrator token, but inference API clients on the same machine can access the loopback endpoint.

Do not expose AMIEBL's API ports to the internet or an untrusted local network, and do not run unknown GGUFs, binaries, installer packages, or generated commands without reviewing their provenance.

For the installed application, configuration, request metadata, and backups are user data. Installer upgrades and uninstalls should preserve these by default.
