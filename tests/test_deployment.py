"""The containers are configured the way the documentation says they are.

The chat app moved from a hand-started host process into Compose on
2026-09-14, because a process started by a terminal — or by a session —
dies with it. Most of what makes that move correct is configuration rather
than code, and configuration is where a well-meant edit silently undoes a
decision: publishing the port to the whole network, pointing a host back at
localhost, or making the app wait on a tracing backend that is supposed to
be optional. These tests read the real files and pin those decisions.

Nothing here needs Docker running.
"""
from __future__ import annotations

import pathlib
import re

import pytest

yaml = pytest.importorskip("yaml")

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE = yaml.safe_load((ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()

APP = SERVICES["chat-api"]


def _env(service: dict) -> dict:
    env = service.get("environment") or {}
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    return {k: str(v) for k, v in env.items()}


# ---------------------------------------------------------------------------
# It stays up
# ---------------------------------------------------------------------------

def test_the_app_is_a_service_that_restarts_itself():
    """The whole point of the move: not tied to whoever started it."""
    assert APP["restart"] == "unless-stopped"


def test_the_app_has_a_healthcheck():
    """Without one, a wedged process still reports "Up"."""
    assert "/health" in " ".join(APP["healthcheck"]["test"])


# ---------------------------------------------------------------------------
# It is reachable only from this machine
# ---------------------------------------------------------------------------

def test_the_app_port_is_bound_to_this_machine_only():
    """⚠️ The demo sign-in is one shared, documented password. Publishing
    the port on every interface would let anyone on the network sign in as
    any seeded user. Opening it up must be a deliberate edit, not a default."""
    ports = [str(p) for p in APP["ports"]]
    assert ports, "the app must publish a port to be usable at all"
    for port in ports:
        assert port.startswith("127.0.0.1:"), f"published beyond this machine: {port}"


def test_the_app_listens_on_all_interfaces_inside_the_container():
    """The host-run default, 127.0.0.1, is unreachable from outside a
    container — Docker's port mapping would forward to nothing."""
    assert _env(APP)["API_HOST"] == "0.0.0.0"


# ---------------------------------------------------------------------------
# It finds its dependencies by service name
# ---------------------------------------------------------------------------

HOST_SETTINGS = ("POSTGRES_SPM_HOST", "REDIS_HOST", "QDRANT_URL",
                 "OTLP_TRACES_ENDPOINT", "LANGFUSE_OTLP_ENDPOINT")


@pytest.mark.parametrize("name", HOST_SETTINGS)
def test_every_dependency_is_addressed_by_service_name(name):
    """infra/.env holds localhost values for host runs. Inside a container
    localhost is the container itself, so each one must be overridden — and
    one missed override is a service that works on the host and not here."""
    value = _env(APP).get(name)
    assert value, f"{name} is not overridden, so the .env localhost value applies"
    assert "localhost" not in value and "127.0.0.1" not in value, f"{name}={value}"


@pytest.mark.parametrize("name", HOST_SETTINGS)
def test_each_named_host_is_a_real_service(name):
    """A typo in a service name resolves to nothing, and tracing fails open,
    so a misspelt tracing host would go unnoticed rather than error."""
    value = _env(APP)[name]
    host = re.sub(r"^[a-z]+://", "", value).split(":")[0].split("/")[0]
    assert host in SERVICES, f"{name} points at '{host}', which is not a service"


def test_the_langfuse_endpoint_uses_its_internal_port():
    """3001 is Langfuse's HOST port; inside the network it listens on 3000."""
    assert ":3000/" in _env(APP)["LANGFUSE_OTLP_ENDPOINT"]


def test_the_app_does_not_wait_on_tracing():
    """Tracing fails open — a tracing outage must never stop an answer. A
    startup dependency on either backend would make exactly that happen."""
    waits_on = set(APP.get("depends_on") or {})
    assert not waits_on & {"langfuse-web", "langfuse-worker", "grafana-lgtm"}
    assert {"postgres-spm", "redis"} <= waits_on


# ---------------------------------------------------------------------------
# The images
# ---------------------------------------------------------------------------

def test_each_service_builds_its_own_target():
    """One Dockerfile, two targets. A service without a target builds the
    LAST stage, so a missing line would quietly run the pipeline worker from
    the app image."""
    assert APP["build"]["target"] == "app"
    for name in ("pipeline-worker", "pipeline-beat"):
        assert SERVICES[name]["build"]["target"] == "pipeline"
    stages = set(re.findall(r"^FROM \S+ AS (\S+)", DOCKERFILE, re.M))
    assert {"base", "pipeline", "app"} <= stages


def test_no_image_runs_as_root():
    for stage in ("pipeline", "app"):
        body = DOCKERFILE.split(f"AS {stage}", 1)[1].split("\nFROM ", 1)[0]
        users = re.findall(r"^USER (\S+)", body, re.M)
        assert users and users[-1] not in ("root", "0"), f"{stage} runs as root"


def test_secrets_are_never_copied_into_an_image():
    """Configuration arrives from Compose at run time. An image layer is not
    a place a key can be taken back out of."""
    assert "infra/.env" in [line.strip() for line in DOCKERIGNORE]
