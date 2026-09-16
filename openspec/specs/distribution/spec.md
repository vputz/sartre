# distribution Specification

## Purpose
Defines how the sartre package presents itself for installation and introspection: its optional-dependency extras reflect only implemented backends, and it exposes its own version at runtime.

## Requirements

### Requirement: Extras reflect only implemented backends
The distribution's optional-dependency extras SHALL correspond to capabilities the package actually provides. It SHALL NOT declare an extra for an unimplemented backend. Specifically, there SHALL be no `delta` extra, since no Delta-Lake registry exists (the SQL and S3-native registries fill that role). The `cli`, `s3`, `gcs`, and `postgres` extras remain.

#### Scenario: No extra advertises an unbuilt backend
- **WHEN** the package metadata's optional dependencies are inspected
- **THEN** no `delta` extra is present, and every declared extra maps to a backend or interface the package implements

### Requirement: Package exposes its version
The package SHALL expose `sartre.__version__` as a non-empty string, sourced from the installed package metadata so that the build's declared version is the single source of truth. When the distribution metadata is unavailable (e.g. running from a raw source checkout), it SHALL fall back to a sentinel version string rather than raise on import.

#### Scenario: Version available when installed
- **WHEN** an installed `sartre` is imported
- **THEN** `sartre.__version__` equals the distribution's declared version

#### Scenario: Import never fails on missing metadata
- **WHEN** `sartre` is imported where its distribution metadata is not present
- **THEN** the import succeeds and `sartre.__version__` is a non-empty sentinel string

### Requirement: Import stays dependency-clean
Exposing the version SHALL NOT introduce any new runtime dependency; it SHALL use only the standard library. Importing `sartre` SHALL continue to require only the core dependency set.

#### Scenario: Core import needs no extra packages
- **WHEN** `sartre` is imported in an environment with only its core dependencies installed
- **THEN** the import succeeds, including reading `sartre.__version__`
