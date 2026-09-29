# common — shared pure helpers

Pure, stateless, side-effect-free helpers used across layers. Rule of thumb: *if it
needs mocking in a test, it does not belong here.*

**One helper so far**: [`loading.py`](loading.py) — `import_callable(target, what=…)`,
which turns a config-named `module:attribute` path into the object it names. It lives
here rather than inside a service because two different extension points use it
(toolset sources *and* link providers) and neither owns the other. It does touch the
import system, so it is worth saying why it still belongs here: no I/O, no state, and
deterministic for a given path — every failure is a `ValueError` naming the config
entry, which is exactly the behaviour under test.

The brain's domain rules are still *not* here: conversation rules live in
`business_domain/`, because they are domain-specific rather than generic utilities.
