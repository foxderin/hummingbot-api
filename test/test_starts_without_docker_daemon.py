"""The API must boot on a host that withholds the Docker socket.

Mounting ``/var/run/docker.sock`` from a rootful daemon gives a container that listens on
a port the ability to stop any other stack on the machine and to start a container with
``-v /:/host``. Deployments that do not need bot or Gateway containers therefore leave it
out — and the API used to die at startup, because two of the three services constructed a
Docker client in ``__init__`` and let the connection error escape.

Three services touch the daemon. This file pins what each one does when it is absent:

* ``DockerService`` reports failure through the ``{"success": False, ...}`` shape its
  routes already turn into a status code.
* ``GatewayService`` reports "no Gateway" — the same answer it gives when the container
  simply does not exist — and refuses to start one without creating files first.
* ``BotsOrchestrator`` loses container discovery, keeping MQTT as the bot source.

Run with: pytest test/test_starts_without_docker_daemon.py -v
"""
import pytest
from docker.errors import DockerException

from models.gateway import GatewayConfig
from services.bots_orchestrator import BotsOrchestrator
from services.docker_service import DockerService
from services.gateway_service import GatewayService


@pytest.fixture
def no_daemon(monkeypatch):
    """docker.from_env() failing the way it does when nothing listens on the socket."""

    def from_env(*args, **kwargs):
        raise DockerException("Error while fetching server API version: no such file or directory")

    import docker

    monkeypatch.setattr(docker, "from_env", from_env)


class TestDockerService:
    def test_constructing_does_not_raise(self, no_daemon):
        service = DockerService()

        assert service.client is None

    def test_listing_containers_reports_a_failure_instead_of_an_attribute_error(self, no_daemon):
        # Reading self.client directly raised AttributeError, which the method's
        # `except DockerException` does not catch -- the route answered 500 with a traceback.
        result = DockerService().get_active_containers()

        assert result["success"] is False
        assert result["error"] == "docker_error"

    def test_the_ping_reports_false(self, no_daemon):
        assert DockerService().is_docker_running() is False

    def test_a_mutating_operation_reports_failure(self, no_daemon):
        result = DockerService().stop_container("bot-1")

        assert result["success"] is False


class TestGatewayService:
    def test_constructing_does_not_raise(self, no_daemon):
        assert GatewayService().client is None

    def test_status_reports_no_gateway(self, no_daemon):
        # Same answer as "the container does not exist", which is what an absent daemon
        # means for a question about a container.
        status = GatewayService().get_status()

        assert status.running is False
        assert status.container_id is None
        assert GatewayService().is_running() is False

    def test_start_refuses_without_creating_gateway_files(self, no_daemon, tmp_path, monkeypatch):
        service = GatewayService()
        monkeypatch.setattr(service, "BOTS_PATH", str(tmp_path))
        monkeypatch.setattr(service, "SOURCE_PATH", str(tmp_path))

        result = service.start(_gateway_config())

        assert result["success"] is False
        assert "Docker daemon" in result["message"]
        # A cert set and conf dirs for a Gateway that can never run would be left behind.
        assert not (tmp_path / "bots" / "gateway-files").exists()

    def test_stop_and_logs_report_not_found(self, no_daemon):
        service = GatewayService()

        assert service.stop()["success"] is False
        assert service.get_logs()["success"] is False


class TestBotsOrchestrator:
    @staticmethod
    def _orchestrator():
        return BotsOrchestrator(
            broker_host="localhost",
            broker_port=1883,
            broker_username="u",
            broker_password="p",
            db_manager=None,
        )

    def test_constructing_does_not_raise(self, no_daemon):
        assert self._orchestrator().docker_client is None

    def test_container_discovery_is_empty_rather_than_an_error(self, no_daemon):
        # Polled once a second; raising here would fill the log with the same traceback.
        assert self._orchestrator()._sync_get_active_containers() == []


def _gateway_config():
    return GatewayConfig()
