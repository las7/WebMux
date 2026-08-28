# Security

## Credential flow

1. The trusted control plane sends a provider key to `POST /v1/credentials`.
2. WebMux encrypts it with AES-256-GCM and stores only ciphertext.
3. The API returns an opaque `cred_<provider>_...` handle.
4. A scheduler exchanges selected handles for a short-lived job capability.
5. The agent calls WebMux with that capability.
6. The trusted adapter resolves the handle and attaches provider authentication in
   memory. The agent receives only normalized results.

Capabilities bind the user, organization, job, provider operations, credential
handles, and expiry. The default TTL is 15 minutes and the V0 maximum is one hour.

Long-lived provider keys must not enter agent environment variables, files,
command-line arguments, prompts, model context, capabilities, responses, or
telemetry.

## Deployment requirements

- Put WebMux behind TLS and authenticated ingress.
- Ingress must remove caller-supplied `X-WebMux-User-Id` and
  `X-WebMux-Org-Id` headers, then set verified values itself.
- Load `WEBMUX_MASTER_KEY` only into the trusted WebMux service. Persist and back it
  up; losing it makes enrolled credentials unrecoverable.
- Restrict access to SQLite because queries and results can contain sensitive data.
- Set a telemetry retention policy before production use.

`WEBMUX_MASTER_KEY` is the service encryption root, not a provider key. WebMux also
derives the capability signing key from it with a domain-separated HMAC.

## V0 limitations

V0 has no master-key rotation workflow, capability revocation list, external KMS,
multi-process coordination, or automated retention/erasure worker. Deleting a
credential prevents its handle from being resolved but currently retains encrypted
database material as a soft deletion.
