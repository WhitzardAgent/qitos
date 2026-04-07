# Security Assessment Report

**Generated:** 2026-04-07 12:06 UTC
**Total Findings:** 1

## Table of Contents

1. [Findings by Severity](#findings-by-severity)
2. [Detailed Findings](#detailed-findings)
3. [ATT&CK Mapping](#mitre-attack-mapping)
4. [Raw Data](#raw-data)

## Findings by Severity

### 🟠 HIGH (1)

- **FIN-0001: Potential command injection via subprocess shell** — app.py

## Detailed Findings

---

### 🟠 FIN-0001: Potential command injection via subprocess shell

| Field | Value |
|-------|-------|
| **Severity** | HIGH |
| **Component** | app.py |

**Description:**

User-controlled command reaches subprocess.run(..., shell=True).

**Evidence:**

```
app.py:8 subprocess.run(request.args.get('cmd'), shell=True)
```

**Remediation:**

Avoid shell=True and validate/allowlist commands.

## MITRE ATT&CK Mapping

No ATT&CK techniques mapped. Add findings with `attack_technique` parameter.

