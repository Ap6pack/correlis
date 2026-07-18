# Contributing

Correlis is evidence-first security software. Contributions must preserve that
property.

## Before opening a pull request

1. Read `docs/ARCHITECTURE.md` and `docs/DATA_MODEL.md`.
2. Add tests for every contract, rule, or state-transition change.
3. Do not place source-specific fields in canonical top-level models without documented compatibility analysis.
4. Do not add an AI model to a detection or correlation decision path.
5. Ensure every derived relationship has a rule ID and evidence references.

## Local checks

```bash
make install
make test
make lint
```

## Architectural changes

Changes to canonical schemas, provenance semantics, storage guarantees, or trust
boundaries must include clear compatibility notes in the pull request and update
the relevant public architecture or data-model documentation.
