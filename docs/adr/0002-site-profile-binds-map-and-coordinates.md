# ADR 0002: Site Profile binds map identity and coordinates

- Status: Accepted
- Date: 2026-07-24

## Context

Saved locations, patrol routes, dock approaches, and image baselines are valid
only in the site and map where they were recorded. Reusing coordinates after a
map or environment change can send a robot to a plausible but incorrect place.

## Decision

JenAI groups site-specific assets in a versioned Site Profile. Every active
profile identifies its map from content evidence and binds that identity to its
locations, routes, dock approaches, reference scene, and validation evidence.

Navigation is blocked when the observed map does not match the activated Site
Profile. A new site must be built, validated, and explicitly activated before
its coordinates are eligible for execution.

## Consequences

- Location names are no longer globally meaningful.
- Copying a location file alone is insufficient to activate a site.
- Operators receive an explicit mismatch instead of silent coordinate reuse.
- Site onboarding requires a guided build and validation flow.
- Validation artifacts can be traced to the exact map and profile version.
