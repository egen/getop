# Security Policy

## Reporting a vulnerability

Please report vulnerabilities privately via
[GitHub's private vulnerability reporting](https://github.com/egen/getop/security/advisories/new)
— do **not** open a public issue for security problems.

You should receive an acknowledgement within a few business days. Please
include reproduction steps and the getop version (`getop --help` header or
`pip show getop`).

## Scope

getop is a strictly read-only client for Google Cloud APIs: it issues only
`list`/`get`-style calls and never mutates cloud resources (enforced by
`tests/test_readonly.py`). Reports we especially care about:

- anything that causes getop to perform a mutating API call
- credential handling flaws (getop uses Application Default Credentials and
  must never read, write, or log key material)
- sensitive log/prompt content ending up anywhere other than the terminal
  the operator invoked getop from

Vulnerabilities in Google Gemini Enterprise itself should go to
[Google's VRP](https://bughunters.google.com), not this project.

## Supported versions

Only the latest release published on [PyPI](https://pypi.org/project/getop/)
is supported.
