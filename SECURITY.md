# Security Policy

## Security Philosophy

ContextBridge is designed to be **safe by default** and operates **100% locally** without telemetry, cloud services, or mandatory network requests.

Core security invariants:

- **Never execute skill content**: Scripts, hooks, or instructions inside `SKILL.md` or `scripts/` are never executed.
- **Never execute MCP servers** or dynamically import untrusted plugins as Python code.
- **Never copy credentials or secrets automatically**: Sensitive filenames (`.env`, private keys, auth configs) are blocked from export and migration, and heuristic scanners alert without exposing secret values.
- **Strict filesystem boundaries**: Path traversal, symlinks, Windows junctions, NTFS Alternate Data Streams (`:`), Windows reserved device names, case-insensitive filename collisions, ZIP Slip, ZIP bombs, and oversized files are rejected.
- **Safe YAML deserialization**: Only a strict `yaml.SafeLoader` subclass is used, rejecting duplicate mapping keys, excessive nesting depth, alias/anchor bombs, and oversized files.
- **Authorized backup restoration**: Manifest-driven restores refuse destinations outside authorized agent skill directories and always block filesystem roots, home roots, the backups store itself, known skills root directories (and their ancestors), and sensitive OS/credential directories (`.ssh`, `.aws`, `.gnupg`, `.kube`, `/etc`, `C:\Windows`). Explicit `--target` overrides also enforce all protected-path checks and refuse overwriting an existing non-empty non-skill directory.
- **Honest integrity scope for manifests**: SHA-256 digests stored in local `manifest.json` files detect accidental corruption, partial writes, and payload-vs-manifest drift. Because local manifests are unsigned, they are **not** a cryptographic authenticity guarantee against an attacker who already holds local write access to `~/.contextbridge/backups`—which is why destination authorization is enforced independently of the manifest.
- **Dry-run by default & TOCTOU guards**: Mutations require explicit `--apply` and never silently overwrite existing files (including ZIP export outputs); migrations re-verify source and destination state between planning and applying, create verified SHA-256 backups before replacing existing targets, and report explicit recovery paths if rollback itself fails.

> **Important limitation**: Static security checks performed by ContextBridge (such as secret detection and dangerous command heuristics) are **heuristic aids only** and cannot guarantee that an untrusted skill is free of malicious intent or prompt injection. Always review skills before installing them into any AI coding agent.

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 0.1.x   | Yes       |

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for security vulnerabilities.

Use [GitHub private vulnerability reporting](https://github.com/AlemaoBoy/ContextBridge/security/advisories/new) or contact the maintainers privately. Include the following details.

1. A clear description of the vulnerability and affected component.
2. Steps to reproduce or a minimal proof of concept.
3. Potential impact and any suggested mitigation.

We aim to acknowledge reports within 72 hours and provide a fix or mitigation plan as quickly as possible.
