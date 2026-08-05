# common — shared pure helpers

**Placeholder (A-Boxed L1 full skeleton).** Intentionally empty for now.

Pure, stateless, side-effect-free helpers go here when they appear — validators,
formatters, converters used across layers. Rule of thumb: *if it needs mocking
in a test, it does not belong here.*

The brain has no such helper yet (the pure rules it does have are conversation
rules, which live in `business_domain/` because they are domain-specific, not
generic utilities).
