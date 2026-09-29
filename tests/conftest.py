"""Test-time defaults, so the suite never depends on a developer's local .env.

``configuration.env`` loads ``.env`` at import, and the composition root builds the
app at import — so on a machine with no ``.env`` a missing variable would fail
*collection* rather than a test. These are placeholders: nothing in the suite
reaches a network.

Set before anything imports ``application.*``. ``load_dotenv`` does not override a
variable that is already present, so these win over a local ``.env`` — which is the
point: the suite has to be reproducible without one.
"""
import os

os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_MODEL", "test-model")
