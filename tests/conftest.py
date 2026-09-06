"""Shared fixtures.

The important one is `patch_server`, and the bug it exists for is worth
understanding before you write another test that patches a server function.

`app/llm.py`, `app/services/search.py`, `app/config_builders.py` and friends all
begin with

    import app.main as main
    sys.modules[__name__].__dict__.update(main.__dict__)

which COPIES main's globals into the importing module, and `app/main.py` then
ends with `from app.llm import *`. The result is that a function like
`route_with_llm` reports `__module__ == "app.main"` while its `__globals__` is
`app.llm`'s dict. So the obvious

    monkeypatch.setattr(m, "fast_llm_json", fake)

rebinds the name in `app.main` only, and `route_with_llm` — resolving
`fast_llm_json` against `app.llm` — never sees it.

That is not a hypothetical. Measured 2026-09-05 on the existing suite: the fake
was called ZERO times and `route_with_llm` made a real call to the Jetson. One
test in the pair failed loudly; its sibling asserted
`[w["type"] for w in plan["widgets"]] == ["converter"]`, which the live model
happened to satisfy, so it PASSED for the wrong reason. Several other files
simply hung for 90-180s making live calls nobody intended.

A mock aimed at a seam the code no longer uses is worse than no mock: the test
still runs, it just tests production. `patch_server` rebinds the name in EVERY
server module whose namespace holds it, so it cannot miss the one the caller
actually resolves against.
"""
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

def _server_modules():
    """Every loaded `app.*` module, discovered rather than listed.

    A hand-maintained list is the same kind of twin that caused the original
    bug: it was missing app.routes.health, so a patch aimed at
    _agent_dependency_status silently missed the only caller that mattered and
    the test went on asserting against the real function. Discovery cannot drift
    when a module is added or a function moves between files.
    """
    return [m for name, m in list(sys.modules.items())
            if m is not None and (name == "app" or name.startswith("app."))]


def _patch_everywhere(monkeypatch, name, value):
    hits = []
    for mod in _server_modules():
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, value, raising=False)
            hits.append(mod.__name__)
    if not hits:
        raise AssertionError(
            f"patch_server({name!r}) matched no server module — the name is "
            "gone or renamed. Fix the test's subject rather than the assertion.")
    return hits


@pytest.fixture
def patch_server(monkeypatch):
    """patch_server('fast_llm_json', fake) — rebind a server function everywhere.

    Returns the list of modules actually patched, so a test can assert it hit
    the one it cares about.
    """
    def _apply(name, value):
        return _patch_everywhere(monkeypatch, name, value)
    return _apply


@pytest.fixture(autouse=True)
def _no_live_models(request, monkeypatch):
    """Fail fast instead of silently calling a real box.

    A unit test that reaches a live vLLM is either slow (90s x N endpoints) or a
    false green. Any test that wants the network must say so with
    @pytest.mark.live.
    """
    if request.node.get_closest_marker("live"):
        return
    import httpx

    # respx patches HTTPCORE (httpcore._async.connection_pool.AsyncConnectionPool
    # et al), which sits BELOW httpx's transport. So a guard installed on
    # httpx.AsyncHTTPTransport.handle_async_request runs in FRONT of respx and
    # would reject the very calls a respx-mocked test set up — measured: it broke
    # test_fast_llm_json_extracts_from_reasoning_models, which mocks
    # 10.0.0.30:8000 legitimately. Stand down whenever respx is intercepting;
    # respx's own assert_all_mocked then covers anything it did not mock.
    _real = httpx.AsyncHTTPTransport.handle_async_request

    def _respx_intercepting():
        # The mocker that actually holds the patches is several subclasses deep
        # (Mocker -> AbstractRequestMocker -> HTTPCoreMocker), so walk the whole
        # tree — checking only direct subclasses silently reports False and the
        # guard then eats respx's own mocked calls.
        try:
            from respx.mocks import Mocker
        except Exception:
            return False

        def _tree(k):
            yield k
            for sub in k.__subclasses__():
                yield from _tree(sub)

        return any(getattr(k, "_patches", None) for k in _tree(Mocker))

    async def _guard(self, request_, *a, **kw):
        if request_.url.host in ("10.0.0.30", "10.0.0.141") and not _respx_intercepting():
            raise AssertionError(
                f"live model call to {request_.url} from a unit test — the mock "
                "did not take. Use the patch_server fixture, or mark the test "
                "@pytest.mark.live if it genuinely needs the box.")
        return await _real(self, request_, *a, **kw)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _guard)


def pytest_configure(config):
    config.addinivalue_line("markers", "live: test genuinely needs a live model box")
