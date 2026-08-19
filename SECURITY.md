# Security policy

## Supported versions

The project is pre-1.0. Security fixes are applied to the latest `0.1.x`
release and the main development branch. Older snapshots may not receive
backports.

## Reporting a vulnerability

Please use the repository's private GitHub security-advisory workflow. Include
the affected version, impact, a minimal reproduction, and any suggested
mitigation. Do not open a public issue until maintainers have coordinated a fix
and disclosure. Avoid attaching sensitive source documents; use a synthetic
fixture that demonstrates the same behavior.

## Deployment considerations

The bundled FastAPI application is a local/reference service. It has no built-in
authentication, authorization, rate limiting, malware scanning, or tenant
isolation. Do not expose it directly to an untrusted network. Put an appropriate
gateway in front of it and set request-size, concurrency, timeout, and resource
limits for the deployment.

Documents and provider output may contain personal, financial, legal, or other
sensitive data. Uploaded files are staged in per-request temporary directories
and removed after the response, but that is not a substitute for encrypted
storage, operating-system isolation, audit controls, or a retention policy.
Logs, crash reports, provider caches, and external inference services can also
retain content.

The upload API rejects provider sidecar paths and renderer file/template
options so a remote caller cannot select arbitrary local files. Trusted
operators who need those SDK-only options should resolve and allowlist paths in
their own application boundary; do not copy them blindly from request JSON.

Treat PDFs, images, DOCX files, fonts, and provider JSON as untrusted input.
Keep Pillow, PyMuPDF, python-docx, FastAPI, and any OCR runtime patched. Run
heavy providers with the least privilege necessary, and do not enable provider
plugins from untrusted sources: plugins execute Python code in the service
process unless a deployment deliberately isolates them.

The framework does not automatically contact an OCR service. A third-party
provider plugin may do so; review its configuration and data-handling terms
before processing private material.
