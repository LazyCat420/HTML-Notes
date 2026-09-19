# HTML-Notes Manifests Specification

Authoritative local manifests defining domain tools, application agent profile, and widget catalog.

## Manifest Files

1. **`html_notes.domain-tools.json`**:
   - Master catalog of all application-owned domain tools.
   - Declares `owner: "html-notes"`, `execution: "local"`, `effect` (`read` | `write` | `destructive`), `resource_type`, `required_scope`, `requires_confirmation`, `legacy_aliases`, `input_schema`, and `result_schema`.
2. **`html_notes.profile.json`**:
   - Application profile registered with `lazy-agent-service`.
   - Declares model constraints, budgets, and strict whitelist of allowed canonical tools and migration aliases.
3. **`html_notes.widget-catalog.json`**:
   - Declarative catalog of server-rendered widgets supported by `app/widgets/factory.py`.
   - Controls widget titles, config schemas, renderer function mapping, and singleton flags.
4. **`global_capabilities_reference.json`**:
   - Read-only reference metadata for global capabilities provided by `lazy-agent-service` (`global.web.search`, `global.web.read_page`, `global.data.transform`).
