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

## Gateway identity

`X-WebMux-User-Id` and `X-WebMux-Org-Id` name the tenant whose vault is read and
whose provider keys are spent, so WebMux does not take them on trust. The gateway
must also send `X-WebMux-Gateway-Signature`:

```text
base64url(HMAC-SHA256(WEBMUX_GATEWAY_SECRET,
    "webmux:v1:gateway-identity:<len(org)>:<org>:<len(user)>:<user>"))
```

WebMux recomputes it and compares with `hmac.compare_digest`. The identity fields
are length-prefixed, so a signature issued for one principal cannot be replayed for
another. With `WEBMUX_GATEWAY_SECRET` unset, identity requests are refused with 503
rather than served open; capability-bearing requests are unaffected.

`WEBMUX_GATEWAY_SECRET` belongs to the gateway and WebMux only. It is a
tenant-impersonation credential: anything holding it can enroll, list, and delete
credentials for any tenant, mint capabilities, and spend any tenant's provider keys.

## Deployment requirements

- Put WebMux behind TLS and authenticated ingress.
- Ingress must remove caller-supplied `X-WebMux-User-Id`, `X-WebMux-Org-Id`, and
  `X-WebMux-Gateway-Signature` headers, then set verified, signed values itself.
- Load `WEBMUX_MASTER_KEY` and `WEBMUX_GATEWAY_SECRET` only into the trusted WebMux
  service and its gateway. Persist and back up the master key; losing it makes
  enrolled credentials unrecoverable.
- Restrict access to SQLite because queries and results can contain sensitive data.
- Set a telemetry retention policy before production use.

`WEBMUX_MASTER_KEY` is the service encryption root, not a provider key. WebMux also
derives the capability signing key from it with a domain-separated HMAC.

## Tenant isolation

- Health is tracked per (tenant, provider). A tenant's authentication failures,
  rate limits, and rejected requests steer only that tenant's routing. Only
  provider-wide outcomes -- timeouts, transport failures, HTTP 5xx, and malformed
  responses -- feed the shared signal every tenant routes on.
- `GET /v1/providers` and `GET /v1/providers/health` require a principal, and the
  health response reports the caller's own measurements, never a cross-tenant
  aggregate.
- `options.provider_options` is allowlisted per provider and can never override a
  field WebMux validates. A capability holder therefore cannot raise the result
  cap, turn on Exa livecrawl, or change the Parallel processor on the credential
  owner's provider account.

## V0 limitations

V0 has no master-key rotation workflow, capability revocation list, external KMS,
multi-process coordination, or automated retention/erasure worker. Deleting a
credential prevents its handle from being resolved but currently retains encrypted
database material as a soft deletion.
