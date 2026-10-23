# Copyright (c) 2026 Project contributors.
"""Integration qualification harness for real and synthetic coding agents."""

from __future__ import annotations

from dataclasses import dataclass
from json import load
from os import environ, pathsep
from pathlib import Path
from secrets import token_hex
from shutil import which
from subprocess import PIPE, CompletedProcess, Popen, run
from sys import executable, stdout
from time import monotonic, sleep
from typing import Final, TypedDict, cast

from .attribution import (
    AttributionDecision,
    AttributionEvidence,
    AttributionResolver,
    AttributionStrength,
)
from .product import state_directory


class ProbePayload(TypedDict):
    """Describe the JSON emitted by the integration probe.

    Attributes:
        token: Correlation token.
        pid: Probe process identifier.
        children: Observed child process identifiers.
        provider: Detected provider.
        provider_session: Provider session identifier.
        surface: Execution surface.
        editor: Editor identity.
        workspace: Workspace path.
        attribution: Attribution evidence source.
        port: Loopback listener port.

    """

    token: str
    pid: int
    children: list[int]
    provider: str
    provider_session: str
    surface: str
    editor: str
    workspace: str
    attribution: str
    port: int


@dataclass(frozen=True, slots=True, kw_only=True)
class IntegrationResult:
    """Describe one integration qualification result.

    Attributes:
        passed: Whether all assertions passed.
        provider: Expected provider.
        token: Probe correlation token.
        detail: Human-readable result detail.

    """

    passed: bool
    provider: str
    token: str
    detail: str


class IntegrationHarness:
    """Run deterministic Lake Terminal qualification scenarios.

    Attributes:
        providers: Supported provider identifiers.
        provider_aliases: Provider aliases resolved to canonical names.
        poll_seconds: Delay between evidence-file checks.
        timeout_seconds: Maximum wait for probe evidence.

    """

    providers: Final[frozenset[str]] = frozenset(
        {"claude", "codex", "cursor", "devin", "human"},
    )
    provider_aliases: Final[dict[str, str]] = {
        "claude-code": "claude",
    }
    poll_seconds: Final[float] = 0.1
    timeout_seconds: Final[float] = 120.0

    @classmethod
    def synthetic(
        cls,
        *,
        home: Path,
        provider: str,
        provider_session: str = "synthetic-session",
    ) -> IntegrationResult:
        """Run one unattended provider-contract qualification.

        Args:
            home: Home directory used for probe evidence.
            provider: Provider identifier.
            provider_session: Synthetic provider session identifier.

        Returns:
            Qualification result.

        """
        cls._validate_provider(provider=provider)
        token: str = token_hex(nbytes=16)
        output: Path = cls._output_path(home=home, token=token)
        environment: dict[str, str] = cls._probe_environment(
            provider=provider,
            provider_session=provider_session,
        )
        result: CompletedProcess[str] = run(
            args=cls.probe_command(token=token, output=output),
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        if result.returncode != 0:
            return IntegrationResult(
                passed=False,
                provider=provider,
                token=token,
                detail=result.stderr.strip() or "probe failed",
            )
        payload: ProbePayload = cls._payload(path=output)
        observed: str = payload["provider"]
        expected: str = "unknown" if provider == "human" else provider
        passed: bool = observed == expected and bool(payload["children"])
        return IntegrationResult(
            passed=passed,
            provider=provider,
            token=token,
            detail=(
                f"provider={observed} children={len(payload['children'])} "
                f"port={payload['port']}"
            ),
        )

    @classmethod
    def real(
        cls,
        *,
        home: Path,
        provider: str,
        timeout_seconds: float | None = None,
    ) -> IntegrationResult:
        """Wait for a real agent to execute a generated probe command.

        Args:
            home: Home directory used for probe evidence.
            provider: Expected real provider identifier.
            timeout_seconds: Maximum time to wait for the agent.

        Returns:
            Qualification result.

        """
        cls._validate_provider(provider=provider)
        token: str = token_hex(nbytes=16)
        output: Path = cls._output_path(home=home, token=token)
        command: str = " ".join(
            cls.probe_command(token=token, output=output),
        )
        _ = stdout.write(
            f"Real {provider} qualification ready.\n"
            "Ask that agent to execute exactly this command and wait for "
            "it to finish:\n\n"
            f"{command}\n\n"
            "Lake is waiting for probe evidence...\n",
        )
        deadline: float = monotonic() + (
            cls.timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        while monotonic() < deadline and not output.is_file():
            sleep(cls.poll_seconds)
        if not output.is_file():
            return IntegrationResult(
                passed=False,
                provider=provider,
                token=token,
                detail="timed out waiting for probe evidence",
            )
        payload: ProbePayload = cls._payload(path=output)
        evidence: AttributionEvidence = AttributionEvidence(
            provider=payload["provider"],
            provider_session=payload["provider_session"],
            source=cls._strength(value=payload["attribution"]),
            surface=payload["surface"],
            editor=payload["editor"],
            workspace=payload["workspace"],
            detail="probe environment",
        )
        decision: AttributionDecision = AttributionResolver.resolve(
            evidence=(evidence,),
        )
        observed_provider: str = cls.provider_aliases.get(
            decision.selected.provider,
            decision.selected.provider,
        )
        passed: bool = observed_provider == provider
        return IntegrationResult(
            passed=passed,
            provider=provider,
            token=token,
            detail=(
                f"observed={decision.selected.provider} "
                f"session={decision.selected.provider_session or '-'} "
                f"source={decision.selected.source.name}"
            ),
        )

    @classmethod
    def concurrent(
        cls,
        *,
        home: Path,
        scenarios: tuple[tuple[str, str], ...],
    ) -> tuple[IntegrationResult, ...]:
        """Run provider probes concurrently in one workspace.

        Args:
            home: Home directory used for probe evidence.
            scenarios: Provider and provider-session pairs.

        Returns:
            Qualification results in scenario order.

        """
        launches: list[tuple[str, str, str, Path, Popen[str]]] = []
        for provider, provider_session in scenarios:
            cls._validate_provider(provider=provider)
            token: str = token_hex(nbytes=16)
            output: Path = cls._output_path(home=home, token=token)
            environment: dict[str, str] = cls._probe_environment(
                provider=provider,
                provider_session=provider_session,
            )
            process: Popen[str] = Popen(
                args=cls.probe_command(token=token, output=output),
                text=True,
                stdout=PIPE,
                stderr=PIPE,
                env=environment,
            )
            launches.append(
                (provider, provider_session, token, output, process),
            )
        results: list[IntegrationResult] = []
        for provider, provider_session, token, output, process in launches:
            error: str = process.communicate(
                timeout=cls.timeout_seconds,
            )[1]
            if process.returncode != 0:
                results.append(
                    IntegrationResult(
                        passed=False,
                        provider=provider,
                        token=token,
                        detail=error.strip() or "probe failed",
                    ),
                )
                continue
            payload: ProbePayload = cls._payload(path=output)
            expected: str = "unknown" if provider == "human" else provider
            results.append(
                IntegrationResult(
                    passed=(
                        payload["provider"] == expected
                        and payload["provider_session"]
                        == ("" if provider == "human" else provider_session)
                    ),
                    provider=provider,
                    token=token,
                    detail=(
                        f"provider={payload['provider']} "
                        f"session={payload['provider_session'] or '-'}"
                    ),
                ),
            )
        return tuple(results)

    @classmethod
    def concurrency_matrix(
        cls,
        *,
        home: Path,
    ) -> tuple[IntegrationResult, ...]:
        """Run the standard multi-provider and same-provider matrix.

        Args:
            home: Home directory used for probe evidence.

        Returns:
            Concurrent qualification results.

        """
        return cls.concurrent(
            home=home,
            scenarios=(
                ("human", ""),
                ("codex", "codex-a"),
                ("codex", "codex-b"),
                ("claude", "claude-a"),
                ("cursor", "cursor-a"),
                ("devin", "devin-a"),
            ),
        )

    @classmethod
    def synthetic_all(cls, *, home: Path) -> tuple[IntegrationResult, ...]:
        """Run all synthetic provider scenarios.

        Args:
            home: Home directory used for probe evidence.

        Returns:
            Results in stable provider order.

        """
        return tuple(
            cls.synthetic(home=home, provider=provider)
            for provider in sorted(cls.providers)
        )

    @staticmethod
    def docker_available() -> bool:
        """Return whether a Docker CLI is available.

        Returns:
            Whether Docker can be invoked.

        """
        return which(cmd="docker") is not None

    @staticmethod
    def sandbox_available() -> bool:
        """Return whether the Docker Sandboxes CLI is available.

        Returns:
            Whether ``sbx`` can be invoked.

        """
        return which(cmd="sbx") is not None

    @staticmethod
    def probe_command(*, token: str, output: Path) -> tuple[str, ...]:
        """Return the exact deterministic probe command.

        Args:
            token: Correlation token.
            output: Evidence output path.

        Returns:
            Command arguments.

        """
        return (
            executable,
            "-m",
            "lake_terminal.integration_probe",
            "--token",
            token,
            "--output",
            str(object=output),
            "--children",
            "2",
            "--http",
        )

    @classmethod
    def _validate_provider(cls, *, provider: str) -> None:
        """Validate a provider identifier.

        Args:
            provider: Provider identifier.

        Raises:
            ValueError: When the provider is unsupported.

        """
        if provider not in cls.providers:
            msg: str = f"Unsupported integration provider: {provider}"
            raise ValueError(msg)

    @staticmethod
    def _probe_environment(
        *,
        provider: str,
        provider_session: str,
    ) -> dict[str, str]:
        """Return a deterministic environment for one probe subprocess.

        Args:
            provider: Provider identifier declared to the probe.
            provider_session: Synthetic provider session identifier.

        Returns:
            Environment with this package importable and provider markers set.

        """
        environment: dict[str, str] = dict(environ)
        root: str = str(object=Path(__file__).resolve().parent.parent)
        inherited: str = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = (
            f"{root}{pathsep}{inherited}" if inherited else root
        )
        environment["LAKE_AGENT"] = provider
        if provider != "human":
            environment["LAKE_AGENT_SESSION"] = provider_session
            environment["LAKE_ATTRIBUTION"] = "provider_hook"
        return environment

    @staticmethod
    def _output_path(*, home: Path, token: str) -> Path:
        """Return the private evidence path for one probe.

        Args:
            home: User home directory.
            token: Correlation token.

        Returns:
            Probe evidence path.

        """
        directory: Path = state_directory(home=home) / "integration"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{token}.json"

    @staticmethod
    def _payload(*, path: Path) -> ProbePayload:
        """Load typed probe evidence.

        Args:
            path: Evidence path.

        Returns:
            Parsed probe payload.

        """
        with path.open(mode="r", encoding="utf-8") as stream:
            return cast(typ="ProbePayload", val=load(fp=stream))

    @staticmethod
    def _strength(*, value: str) -> AttributionStrength:
        """Convert persisted attribution text to evidence strength.

        Args:
            value: Attribution source text.

        Returns:
            Evidence strength.

        """
        normalized: str = value.strip().lower().replace("-", "_")
        return {
            "execution_token": AttributionStrength.execution_token,
            "provider_hook": AttributionStrength.provider_hook,
            "environment": AttributionStrength.environment,
            "editor": AttributionStrength.editor,
            "process_tree": AttributionStrength.process_tree,
            "heuristic": AttributionStrength.heuristic,
        }.get(normalized, AttributionStrength.unknown)
