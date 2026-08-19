# Schemas

- `document-ir.schema.json` describes canonical IR schema version `0.1`.
- `analyze-options.schema.json` describes `POST /v1/analyze` options.
- `reconstruct-options.schema.json` describes the JSON string carried in the
  multipart `options` field of `POST /v1/reconstruct`.
- `route-options.schema.json` describes selective provider routing options.
- `compare-options.schema.json` describes fidelity comparison options.

The Pydantic models in `docreconstruct.ir` and `docreconstruct.api.models` are
the runtime authority. Cross-field constraints such as `bbox.x1 >= bbox.x0`,
finite coordinates, unique page identifiers, and a path-free output filename
are enforced by those models in addition to the portable JSON Schema rules.
