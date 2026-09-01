# Provider adapters

Each adapter implements `SearchProvider.search(query, options, credential_handle)`.
It owns provider authentication, request translation, response validation, and
normalization into `SearchResult`.

Provider-specific response fields belong in `provider_metadata`; adapters should not
invent equivalents for fields the provider does not supply. Errors must use the
shared provider exceptions so fallback and telemetry behave consistently.

## V0 providers

- [Brave Web Search](https://api-dashboard.search.brave.com/api-reference/web/search/get)
- [Exa Search](https://exa.ai/docs/reference/search)
- [Parallel Search](https://docs.parallel.ai/api-reference/search/search)

## Add a provider

1. Add an adapter beside the existing providers.
2. Export it from `providers/__init__.py`.
3. Register its class in `Container.build`.
4. Add pricing, timeout, and initial latency configuration.
5. Set `allowed_provider_options` to the safe caller-supplied keys, and merge them
   with `_extra_options` so they cannot override a validated field or raise cost.
6. Extend the accepted provider names and benchmark provider list.
7. Add normalization, authentication, malformed-response, and routing tests.

Provider keys are resolved from opaque handles only inside trusted adapter code.
