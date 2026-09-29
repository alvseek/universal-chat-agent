"""External integration: a machine credential from an OIDC identity provider.

Reference adapter, written against a self-hosted Logto. It speaks only the
standard ``client_credentials`` grant, so any issuer that mints a token bound to
a resource (RFC 8707) works unchanged.
"""
