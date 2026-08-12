"""Application orchestration across transport, protocol, domain, and persistence boundaries."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from codex_workbench import __version__
from codex_workbench.app_server.correlation import (
    AppServerDisconnectedError,
    AppServerRequestError,
    AppServerRequestTimeoutError,
)
from codex_workbench.app_server.protocol import ProtocolAdapter
from codex_workbench.app_server.supervisor import AppServerSupervisor, ProcessState
from codex_workbench.app_server.transport import JsonObject, JsonScalar, JsonValue
from codex_workbench.config import Settings
from codex_workbench.deploy_keys import DeployKeyService
from codex_workbench.domain.approvals import (
    ApprovalRecord,
    ApprovalRegistry,
    ApprovalReplayError,
    ApprovalUnavailableError,
)
from codex_workbench.domain.capabilities import FullAccessRegistry
from codex_workbench.domain.projection import DomainProjection, ProjectedThread
from codex_workbench.git_service import (
    CloneTargetReservation,
    GitOperationError,
    GitResult,
    GitService,
)
from codex_workbench.host_capabilities import detect_sandbox_capabilities
from codex_workbench.jobs import BackgroundJobRegistry
from codex_workbench.persistence.metadata import MetadataRepository, ThreadReference, ThreadTitle
from codex_workbench.realtime.broker import RealtimeBroker
from codex_workbench.repositories import RepositoryDiscovery, RepositorySummary
from codex_workbench.security.rate_limit import LoginRateLimiter, RateLimitDecision
from codex_workbench.service_status import ComponentStatus, ServiceState, SystemStatus
from codex_workbench.uploads import UploadService

logger = logging.getLogger("codex_workbench.runtime")


@dataclass(frozen=True, slots=True)
class CapabilityCacheEntry:
    generation: int
    expires_at: float
    value: JsonObject


@dataclass(frozen=True, slots=True)
class CapabilityCircuit:
    error_class: str
    generation: int


class RuntimeUnavailableError(RuntimeError):
    pass


class RuntimeConflictError(RuntimeError):
    pass


class ThreadUnavailableError(RuntimeError):
    pass


class WorkbenchRuntime:
    def __init__(self, settings: Settings, metadata: MetadataRepository) -> None:
        self.settings = settings
        self.metadata = metadata
        self.repositories = RepositoryDiscovery(settings.repositories_root)
        self.deploy_keys = DeployKeyService(settings, metadata)
        self.git = GitService(
            self.repositories,
            settings.repositories_root,
            output_limit=settings.max_command_output_bytes,
            command_timeout_seconds=settings.git_command_timeout_seconds,
            network_timeout_seconds=settings.git_network_timeout_seconds,
            clone_timeout_seconds=settings.git_clone_timeout_seconds,
            deploy_keys=self.deploy_keys,
        )
        self.host_capabilities: JsonObject = {}
        self.broker = RealtimeBroker(
            history_size=settings.realtime_history_size,
            subscriber_queue_size=settings.realtime_client_queue_size,
        )
        self.jobs = BackgroundJobRegistry(update_handler=self._on_job_updated)
        self.projection = DomainProjection(
            self.broker,
            metadata,
            max_output_bytes=settings.max_command_output_bytes,
        )
        self.approvals = ApprovalRegistry(ttl_seconds=settings.approval_ttl_seconds)
        self.full_access = FullAccessRegistry(
            ttl_seconds=settings.full_access_confirmation_ttl_seconds
        )
        self.uploads = UploadService(settings, metadata)
        self.protocol = ProtocolAdapter()
        self.supervisor = AppServerSupervisor(
            settings,
            notification_handler=self._on_notification,
            server_request_handler=self._on_server_request,
            disconnect_handler=self._on_disconnect,
            ready_handler=self._on_ready,
            terminal_failure_handler=self._on_terminal_failure,
        )
        self._thread_mutations: dict[str, asyncio.Lock] = {}
        self._approval_expiry_tasks: dict[str, asyncio.Task[None]] = {}
        self._title_refresh_tasks: dict[str, asyncio.Task[None]] = {}
        self._capability_cache: dict[str, CapabilityCacheEntry] = {}
        self._capability_flights: dict[str, asyncio.Task[JsonObject]] = {}
        self._capability_circuits: dict[tuple[str, str], CapabilityCircuit] = {}
        self._capability_transport_probes: dict[int, dict[str, int]] = {}
        self._capability_lock = asyncio.Lock()
        self._reconciliation_failures: set[str] = set()
        self._unavailable_threads: dict[str, tuple[str, JsonObject]] = {}
        self._mutation_limiter = LoginRateLimiter(
            address_limit=settings.mutation_rate_limit_requests,
            global_limit=settings.mutation_rate_limit_global_requests,
            window_seconds=settings.mutation_rate_limit_window_seconds,
        )
        self._manual_restart_session_id: str | None = None

    @property
    def app_server_ready(self) -> bool:
        return self.supervisor.status.state is ProcessState.READY

    async def start(self) -> None:
        await self.deploy_keys.initialize()
        await self.uploads.initialize()
        # Protocol integration tests use a fake child and must not inherit unrelated host sandbox
        # constraints. The detector itself has fixture coverage; deployed environments are probed.
        if self.settings.environment == "test":
            self.host_capabilities = {
                "sandbox": {
                    "workspaceWrite": {"available": True, "reason": None},
                    "readOnly": {"available": True, "reason": None},
                    "fullAccess": {"available": True, "reason": None},
                }
            }
        else:
            self.host_capabilities = await detect_sandbox_capabilities()
        if self.settings.app_server_start_enabled:
            await self.projection.connection_state("connecting", 0)
            await self.supervisor.start()
        else:
            await self.projection.connection_state("disconnected", 0, "disabled")

    async def stop(self) -> None:
        await self.jobs.stop()
        for task in self._title_refresh_tasks.values():
            task.cancel()
        if self._title_refresh_tasks:
            await asyncio.gather(*self._title_refresh_tasks.values(), return_exceptions=True)
        self._title_refresh_tasks.clear()
        for task in self._approval_expiry_tasks.values():
            task.cancel()
        if self._approval_expiry_tasks:
            await asyncio.gather(*self._approval_expiry_tasks.values(), return_exceptions=True)
        self._approval_expiry_tasks.clear()
        await self.supervisor.stop()

    async def list_repositories(self) -> tuple[RepositorySummary, ...]:
        repositories = await self.repositories.list_repositories()
        hidden = await asyncio.to_thread(self.metadata.hidden_repository_ids)
        return tuple(repository for repository in repositories if repository.id not in hidden)

    async def create_repository(self, name: str, *, readme: bool) -> GitResult:
        result = await self.git.create(name, readme=readme)
        if result.exit_code == 0:
            await self._mark_created_repository_available(name)
        return result

    async def clone_repository(self, name: str, url: str) -> GitResult:
        result = await self.git.clone(name, url)
        if result.exit_code == 0:
            await self._mark_created_repository_available(name)
        return result

    async def clone_private_repository(
        self, reservation: CloneTargetReservation, credential_id: str
    ) -> GitResult:
        result = await self.git.clone_with_deploy_key(reservation, credential_id)
        if result.exit_code == 0:
            await self._mark_created_repository_available(reservation.name)
        return result

    async def _mark_created_repository_available(self, name: str) -> RepositorySummary:
        """Reactivate one freshly discovered direct-child repository, never a client ID."""
        expected_id = GitService.repository_id_for_name(name)
        try:
            summary, path = await asyncio.to_thread(
                self.repositories.resolve_repository_id, expected_id
            )
            root = self.settings.repositories_root.resolve(strict=True)
            resolved = path.resolve(strict=True)
        except (OSError, ValueError) as exc:
            raise GitOperationError("created repository validation failed") from exc
        if (
            summary.id != expected_id
            or summary.name != name
            or summary.relative_path != name
            or path.is_symlink()
            or resolved == root
            or resolved.parent != root
        ):
            raise GitOperationError("created repository identity does not match")
        try:
            await asyncio.to_thread(
                self.metadata.mark_repository_available,
                summary.id,
                summary.relative_path,
            )
        except (RuntimeError, ValueError) as exc:
            raise GitOperationError("repository reactivation failed") from exc
        return summary

    async def repository_removal_preview(
        self, repository_id: str, session_id: str
    ) -> JsonObject:
        summary, _path = await asyncio.to_thread(
            self.repositories.resolve_repository_id, repository_id
        )
        status = await self.git.status(repository_id)
        references = await asyncio.to_thread(
            self.metadata.list_repository_threads, repository_id
        )
        blockers: list[JsonValue] = []
        if references:
            await self._reconcile()
            if any(reference.thread_id in self._unavailable_threads for reference in references):
                blockers.append("turn_state_unknown")
        if self.projection.repository_has_active_turn(repository_id):
            blockers.append("active_turn")
        if await self.jobs.has_active_repository_job(repository_id):
            blockers.append("active_job")
        dirty = not bool(status.get("clean", False))
        return {
            "repositoryId": summary.id,
            "repositoryName": summary.name,
            "relativePath": summary.relative_path,
            "threadCount": len(references),
            "hasUncommittedChanges": dirty,
            "blockedReasons": blockers,
            "canRemove": not blockers,
            "canDelete": not blockers,
            "sessionBound": bool(session_id),
        }

    async def remove_repository(
        self,
        repository_id: str,
        session_id: str,
        *,
        confirmation: str,
        delete_threads: bool,
        delete_local: bool,
    ) -> JsonObject:
        summary, _path = await asyncio.to_thread(
            self.repositories.resolve_repository_id, repository_id
        )
        if confirmation != summary.name:
            raise RuntimeConflictError("repository confirmation does not match")
        if not await self.jobs.reserve_repository_removal(repository_id):
            raise RuntimeConflictError("repository has an active job")
        try:
            if self.projection.repository_has_active_turn(repository_id):
                raise RuntimeConflictError("repository has an active turn")
            references = await asyncio.to_thread(
                self.metadata.list_repository_threads, repository_id
            )
            if references:
                await self._reconcile()
                if any(
                    reference.thread_id in self._unavailable_threads
                    for reference in references
                ):
                    raise RuntimeConflictError("repository turn state is unavailable")
                if self.projection.repository_has_active_turn(repository_id):
                    raise RuntimeConflictError("repository has an active turn")
            if delete_threads:
                for reference in references:
                    await self._call(
                        "thread/delete",
                        {"threadId": reference.thread_id},
                        owner=session_id,
                    )
                for reference in references:
                    await asyncio.to_thread(self.metadata.delete_thread, reference.thread_id)
                    self.projection.forget_thread(reference.thread_id)
            if delete_local:
                await asyncio.to_thread(self.repositories.delete_repository, repository_id)
            self.projection.mark_repository_unavailable(repository_id)
            await asyncio.to_thread(
                self.metadata.mark_repository_unavailable,
                repository_id,
                summary.relative_path,
                state="deleted" if delete_local else "removed",
                updated_at=int(time.time()),
            )
            await asyncio.to_thread(
                self.metadata.audit,
                "repository_deleted" if delete_local else "repository_removed",
                occurred_at=int(time.time()),
                session_id=session_id,
                repository_id=repository_id,
            )
            return {
                "repositoryId": repository_id,
                "state": "deleted" if delete_local else "removed",
                "threadsDeleted": len(references) if delete_threads else 0,
            }
        finally:
            await self.jobs.release_repository_removal(repository_id)

    async def repository_browser_dict(self, summary: RepositorySummary) -> JsonObject:
        preference = await asyncio.to_thread(self.metadata.repository_preferences, summary.id)
        return {
            "id": summary.id,
            "name": (preference or {}).get("display_name") or summary.name,
            "directoryName": summary.name,
            "status": self._json_object(await self.git.status(summary.id)),
            "preferences": {
                "model": (preference or {}).get("model"),
                "effort": (preference or {}).get("reasoning_effort"),
            },
            "renameCapabilities": {"displayName": True, "directory": False, "remote": False},
        }

    async def update_repository_preferences(
        self,
        repository_id: str,
        *,
        display_name: str | None,
        model: str | None,
        effort: str | None,
    ) -> JsonObject:
        summary, _path = await asyncio.to_thread(
            self.repositories.resolve_repository_id, repository_id
        )
        if display_name is not None:
            display_name = display_name.strip()
            if (
                not display_name
                or len(display_name) > 80
                or any(char in display_name for char in "\x00\r\n")
            ):
                raise ValueError("invalid display name")
        await asyncio.to_thread(
            self.metadata.upsert_repository_preferences,
            repository_id,
            summary.relative_path,
            display_name=display_name,
            model=model,
            reasoning_effort=effort,
            updated_at=int(time.time()),
        )
        return await self.repository_browser_dict(summary)

    def consume_mutation(self, session_id: str) -> RateLimitDecision:
        return self._mutation_limiter.consume(session_id)

    async def system_status(self) -> SystemStatus:
        """Build an authenticated, content-free operator status snapshot."""

        observed_at = int(time.time())
        supervisor = self.supervisor.status
        app_state = {
            ProcessState.READY: ServiceState.HEALTHY,
            ProcessState.RESTARTING: ServiceState.RECOVERING,
            ProcessState.CHECKING: ServiceState.RECOVERING,
            ProcessState.STARTING: ServiceState.RECOVERING,
            ProcessState.INITIALIZING: ServiceState.RECOVERING,
            ProcessState.FAILED: ServiceState.FAILED,
            ProcessState.DRAINING: ServiceState.BLOCKED,
            ProcessState.STOPPED: ServiceState.BLOCKED,
        }[supervisor.state]
        if not self.settings.app_server_start_enabled:
            app_state = ServiceState.BLOCKED
        elif app_state is ServiceState.HEALTHY and supervisor.error_code is not None:
            app_state = ServiceState.DEGRADED

        database_ready = await asyncio.to_thread(self.metadata.database_quick_check)
        repository_state = self._storage_state(self.settings.repositories_root)
        upload_state = self._storage_state(self.settings.uploads_dir)
        _stream_count, connection_count = self.broker.status_counts()
        job_status = await self.jobs.status_counts()
        jobs_state = (
            ServiceState.DEGRADED
            if job_status.infrastructure_error_code is not None
            else ServiceState.HEALTHY
        )
        plugin_state = (
            ServiceState.BLOCKED
            if not self.settings.plugin_discovery_enabled
            else ServiceState.DEGRADED
            if any(key[0] == "plugin/list" for key in self._capability_circuits)
            else ServiceState.HEALTHY
        )
        components = (
            ComponentStatus(
                component="backend_http",
                state=ServiceState.HEALTHY,
                observedAt=observed_at,
                lastSuccessAt=observed_at,
            ),
            ComponentStatus(
                component="codex_app_server",
                state=app_state,
                observedAt=observed_at,
                errorCode=supervisor.error_code,
                lastSuccessAt=supervisor.last_ready_at,
                lastRecoveryAt=supervisor.last_recovery_at,
                nextRecoveryAt=supervisor.next_recovery_at,
                generation=supervisor.generation,
                restartAttempts=supervisor.restart_attempts,
                restartLimit=self.settings.app_server_restart_limit,
                enabled=self.settings.app_server_start_enabled,
            ),
            ComponentStatus(
                component="realtime_websocket",
                state=(
                    ServiceState.HEALTHY
                    if connection_count > 0
                    else ServiceState.UNKNOWN
                ),
                observedAt=observed_at,
                connectionCount=connection_count,
            ),
            ComponentStatus(
                component="sqlite_metadata",
                state=ServiceState.HEALTHY if database_ready else ServiceState.FAILED,
                observedAt=observed_at,
                errorCode=None if database_ready else "database_quick_check_failed",
            ),
            ComponentStatus(
                component="repository_root",
                state=repository_state,
                observedAt=observed_at,
                errorCode=(
                    "storage_unavailable"
                    if repository_state is ServiceState.FAILED
                    else "storage_capacity_low"
                    if repository_state is ServiceState.DEGRADED
                    else None
                ),
            ),
            ComponentStatus(
                component="upload_storage",
                state=upload_state,
                observedAt=observed_at,
                errorCode=(
                    "storage_unavailable"
                    if upload_state is ServiceState.FAILED
                    else "storage_capacity_low"
                    if upload_state is ServiceState.DEGRADED
                    else None
                ),
            ),
            ComponentStatus(
                component="background_jobs",
                state=jobs_state,
                observedAt=observed_at,
                errorCode=job_status.infrastructure_error_code,
                activeCount=job_status.active,
                queuedCount=job_status.queued,
                failedCount=job_status.failed,
            ),
            ComponentStatus(
                component="plugin_discovery",
                state=plugin_state,
                observedAt=observed_at,
                errorCode=(
                    "plugin_discovery_degraded"
                    if plugin_state is ServiceState.DEGRADED
                    else None
                ),
                optional=True,
                enabled=self.settings.plugin_discovery_enabled,
            ),
        )
        required_states = [component.state for component in components if not component.optional]
        overall = (
            ServiceState.FAILED
            if ServiceState.FAILED in required_states
            else ServiceState.RECOVERING
            if ServiceState.RECOVERING in required_states
            else ServiceState.DEGRADED
            if ServiceState.DEGRADED in required_states
            else ServiceState.BLOCKED
            if ServiceState.BLOCKED in required_states
            else ServiceState.HEALTHY
        )
        return SystemStatus(
            status=overall,
            workbenchVersion=__version__,
            codexVersion=supervisor.version,
            observedAt=observed_at,
            components=components,
        )

    async def restart_app_server(self, session_id: str, confirmation: str) -> str:
        if confirmation != "CODEX RUNTIME NEU STARTEN":
            raise ValueError("manual restart confirmation does not match")
        if not self.settings.app_server_start_enabled:
            raise RuntimeUnavailableError("app-server startup is disabled")
        if self.projection.has_active_turns():
            raise RuntimeConflictError(
                "Die Codex Runtime kann während eines aktiven Turns nicht neu gestartet werden."
            )
        try:
            result = await self.supervisor.request_restart()
        except RuntimeError as exc:
            raise RuntimeUnavailableError("app-server restart is unavailable") from exc
        if result == "blocked":
            raise RuntimeUnavailableError("app-server restart budget is exhausted")
        generation = self.supervisor.status.generation
        self._manual_restart_session_id = session_id
        await self.projection.connection_state("reconnecting", generation, "manual_restart")
        await asyncio.to_thread(
            self.metadata.audit,
            "app_server_restart_requested",
            occurred_at=int(time.time()),
            session_id=session_id,
        )
        logger.warning(
            "manual_runtime_restart_requested",
            extra={
                "component": "codex_app_server",
                "generation": generation,
                "state": "recovering",
            },
        )
        return result

    async def set_thread_title(
        self, thread_id: str, session_id: str, display_title: str
    ) -> JsonObject:
        """Set one native title and persist manual precedence across reconciliation."""

        thread = self.projection.require_thread(thread_id)
        if thread.session_id != session_id:
            raise ThreadUnavailableError("thread is unavailable")
        title = " ".join(display_title.split()).strip(" \t\r\n\"'`#*_~")
        title = title.removesuffix("...").strip()
        if not title or len(title) > 80:
            raise ValueError("thread title is invalid")
        async with self._thread_mutations.setdefault(thread_id, asyncio.Lock()):
            await self._call(
                "thread/name/set",
                {"threadId": thread_id, "name": title},
                owner=session_id,
            )
            updated_at = int(time.time())
            stored = await asyncio.to_thread(
                self.metadata.set_thread_title,
                thread_id,
                title,
                source="manual",
                updated_at=updated_at,
            )
            browser = await self.projection.apply_thread_title(
                thread_id,
                display_title=stored.display_title,
                source=stored.source,
                updated_at=stored.updated_at,
                generation_state=stored.generation_state,
                error_code=stored.error_code,
            )
        await self.broker.publish(
            session_id,
            "thread.name",
            {
                "name": stored.display_title,
                "titleSource": stored.source,
                "titleUpdatedAt": stored.updated_at,
                "titleGenerationState": stored.generation_state,
                "titleErrorCode": stored.error_code,
            },
            thread_id=thread_id,
        )
        return browser

    def _storage_state(self, path: Path) -> ServiceState:
        if not path.is_dir() or not os.access(path, os.R_OK | os.W_OK | os.X_OK):
            return ServiceState.FAILED
        try:
            free = shutil.disk_usage(path).free
        except OSError:
            return ServiceState.FAILED
        return (
            ServiceState.DEGRADED
            if free < self.settings.storage_warning_free_bytes
            else ServiceState.HEALTHY
        )

    async def capabilities(
        self,
        repository_id: str | None,
        session_id: str,
        *,
        refresh: bool = False,
        tools: Literal["none", "skills", "plugins", "all"] = "all",
    ) -> JsonObject:
        """Return cached, fail-soft, schema-backed browser capability metadata."""
        cwd: str | None = None
        if repository_id is not None:
            _summary, path = await asyncio.to_thread(
                self.repositories.resolve_repository_id, repository_id
            )
            cwd = str(path)
        key = f"{repository_id or '__global__'}:{tools}"
        generation = self.supervisor.status.generation
        now = time.monotonic()
        async with self._capability_lock:
            cached = self._capability_cache.get(key)
            if refresh:
                self._capability_cache.pop(key, None)
            elif (
                cached is not None
                and cached.generation == generation
                and cached.expires_at > now
            ):
                return self._capability_cache_view(cached.value, cached=True, stale=False)
            elif cached is not None and not self.app_server_ready:
                return self._capability_cache_view(cached.value, cached=True, stale=True)
            flight = self._capability_flights.get(key)
            if flight is None:
                flight = asyncio.create_task(
                    self._discover_capabilities(key, cwd, session_id, generation, tools),
                    name=f"capability-discovery-{key[-8:]}",
                )
                self._capability_flights[key] = flight
                flight.add_done_callback(
                    lambda completed: self._complete_capability_flight(
                        key, generation, completed
                    )
                )
        value = await asyncio.shield(flight)
        return self._capability_cache_view(value, cached=False, stale=False)

    def _complete_capability_flight(
        self, key: str, generation: int, task: asyncio.Task[JsonObject]
    ) -> None:
        if self._capability_flights.get(key) is task:
            self._capability_flights.pop(key, None)
        if task.cancelled() or self.supervisor.status.generation != generation:
            return
        try:
            value = task.result()
        except Exception:
            return
        self._capability_cache[key] = CapabilityCacheEntry(
            generation=generation,
            expires_at=time.monotonic() + self.settings.capability_cache_ttl_seconds,
            value=value,
        )

    async def _discover_capabilities(
        self,
        key: str,
        cwd: str | None,
        session_id: str,
        generation: int,
        tools: Literal["none", "skills", "plugins", "all"],
    ) -> JsonObject:
        states: JsonObject = {}
        models_result, model_reason = await self._capability_request(
            key,
            "model_list",
            "model/list",
            {"includeHidden": False},
            session_id,
            generation,
        )
        models_raw = models_result.get("data") if models_result is not None else None
        models: list[JsonValue] = []
        if isinstance(models_raw, list):
            for value in models_raw:
                if not isinstance(value, dict) or value.get("hidden") is True:
                    continue
                model = value.get("model")
                if not isinstance(model, str):
                    continue
                efforts: list[JsonValue] = []
                for option in self._array(value.get("supportedReasoningEfforts")):
                    if isinstance(option, dict) and isinstance(option.get("reasoningEffort"), str):
                        efforts.append(
                            {
                                "value": option["reasoningEffort"],
                                "description": option.get("description")
                                if isinstance(option.get("description"), str)
                                else "",
                            }
                        )
                models.append(
                    {
                        "id": model,
                        "displayName": value.get("displayName")
                        if isinstance(value.get("displayName"), str)
                        else model,
                        "description": value.get("description")
                        if isinstance(value.get("description"), str)
                        else "",
                        "isDefault": value.get("isDefault") is True,
                        "defaultReasoningEffort": value.get("defaultReasoningEffort"),
                        "reasoningEfforts": efforts,
                        "inputModalities": [
                            item
                            for item in self._array(value.get("inputModalities"))
                            if isinstance(item, str)
                        ],
                    }
                )
        states["models"] = self._capability_state(model_reason)
        skills: list[JsonValue] = []
        plugins: list[JsonValue] = []
        skill_reason: str | None = "not_loaded"
        plugin_reason: str | None = (
            "not_loaded" if self.settings.plugin_discovery_enabled else "disabled"
        )
        if cwd is not None and tools in {"skills", "all"}:
            skill_payload, skill_reason = await self._capability_request(
                key,
                "skills_list",
                "skills/list",
                {"cwds": [cwd], "forceReload": False},
                session_id,
                generation,
            )
            for entry in self._array(
                skill_payload.get("data") if skill_payload is not None else None
            ):
                if not isinstance(entry, dict):
                    continue
                for skill in self._array(entry.get("skills")):
                    if isinstance(skill, dict) and isinstance(skill.get("name"), str):
                        skills.append(
                            {
                                "name": skill["name"],
                                "description": self._safe_string(
                                    skill.get("shortDescription") or skill.get("description")
                                ),
                                "enabled": skill.get("enabled") is True,
                            }
                        )
        if (
            cwd is not None
            and tools in {"plugins", "all"}
            and self.settings.plugin_discovery_enabled
        ):
            plugin_payload, plugin_reason = await self._capability_request(
                key,
                "plugin_list",
                "plugin/list",
                {"cwds": [cwd]},
                session_id,
                generation,
            )
            for marketplace in self._array(
                plugin_payload.get("marketplaces") if plugin_payload is not None else None
            ):
                if not isinstance(marketplace, dict):
                    continue
                for plugin in self._array(marketplace.get("plugins")):
                    if isinstance(plugin, dict) and isinstance(plugin.get("id"), str):
                        plugins.append(
                            {
                                "id": plugin["id"],
                                "name": self._safe_string(plugin.get("name")) or plugin["id"],
                                "installed": plugin.get("installed") is True,
                                "enabled": plugin.get("enabled") is True,
                            }
                        )
        states["skills"] = self._capability_state(skill_reason)
        states["plugins"] = self._capability_state(plugin_reason)
        sandbox = self.host_capabilities.get("sandbox")
        sandbox_values = list(sandbox.values()) if isinstance(sandbox, dict) else []
        sandbox_available = any(
            isinstance(value, dict) and value.get("available") is True
            for value in sandbox_values
        )
        overall = (
            "unavailable"
            if model_reason is not None or not sandbox_available
            else "ready"
            if all(
                isinstance(value, dict) and value.get("available") is True
                for value in sandbox_values
            )
            else "degraded"
        )
        return {
            "models": models,
            "skills": skills,
            "plugins": plugins,
            "native": {
                "models": model_reason is None,
                "skills": skill_reason is None,
                "plugins": plugin_reason is None,
                "apps": False,
            },
            "host": self.host_capabilities,
            "status": {
                "overall": overall,
                "generation": generation,
                "cached": False,
                "stale": False,
                **states,
                "apps": {"state": "disabled", "available": False, "reason": None},
            },
        }

    async def _capability_request(
        self,
        key: str,
        capability: str,
        method: str,
        params: JsonObject,
        owner: str,
        generation: int,
    ) -> tuple[JsonObject | None, str | None]:
        del key
        circuit_key = self._capability_circuit_key(capability)
        if circuit_key in self._capability_circuits:
            logger.info(
                "capability_circuit_skipped",
                extra={
                    "capability": capability,
                    "error_class": "transport_failure",
                    "generation": generation,
                    "codex_version": circuit_key[0],
                },
            )
            return None, "temporarily_disabled"
        transport_probe = capability == "plugin_list"
        if transport_probe:
            probes = self._capability_transport_probes.setdefault(generation, {})
            probes[capability] = probes.get(capability, 0) + 1
        try:
            result = await self._call_for_generation(
                generation, method, params, owner=owner
            )
            if not isinstance(result, dict):
                raise ValueError("invalid capability response")
            return result, None
        except AppServerRequestError as exc:
            if exc.code == -32601:
                reason = "unsupported"
            elif exc.code == -32602:
                reason = "invalid_params"
            else:
                reason = "json_rpc_error"
        except AppServerRequestTimeoutError:
            reason = "timeout"
        except AppServerDisconnectedError:
            reason = "transport_failure"
            if transport_probe:
                self._open_capability_circuit(capability, generation)
        except (RuntimeUnavailableError, ConnectionError):
            reason = "runtime_unavailable"
        except Exception:
            reason = "invalid_response"
        finally:
            if transport_probe:
                active_probes = self._capability_transport_probes.get(generation)
                if active_probes is not None:
                    remaining = active_probes.get(capability, 0) - 1
                    if remaining > 0:
                        active_probes[capability] = remaining
                    else:
                        active_probes.pop(capability, None)
                    if not active_probes:
                        self._capability_transport_probes.pop(generation, None)
        logger.warning(
            "capability_request_failed",
            extra={"capability": capability, "error_class": reason},
        )
        return None, reason

    def _capability_circuit_key(self, capability: str) -> tuple[str, str]:
        version = self.supervisor.status.version or self.settings.expected_codex_version
        # The runtime object is process-scoped. A generation change keeps the circuit; only a
        # newly observed Codex version (or constructing a new runtime on backend restart) resets it.
        for key in tuple(self._capability_circuits):
            if key[1] != capability or key[0] == version:
                continue
            circuit = self._capability_circuits.pop(key)
            logger.info(
                "capability_circuit_reset",
                extra={
                    "capability": capability,
                    "error_class": circuit.error_class,
                    "generation": self.supervisor.status.generation,
                    "codex_version": version,
                },
            )
        return version, capability

    def _open_capability_circuit(self, capability: str, generation: int) -> None:
        key = self._capability_circuit_key(capability)
        if key in self._capability_circuits:
            return
        self._capability_circuits[key] = CapabilityCircuit(
            error_class="transport_failure", generation=generation
        )
        logger.warning(
            "capability_circuit_opened",
            extra={
                "capability": capability,
                "error_class": "transport_failure",
                "generation": generation,
                "codex_version": key[0],
            },
        )

    @staticmethod
    def _capability_state(reason: str | None) -> JsonObject:
        if reason in {"disabled", "not_loaded"}:
            return {"state": reason, "available": False, "reason": None}
        return {
            "state": "ready" if reason is None else "unavailable",
            "available": reason is None,
            "reason": reason,
        }

    @staticmethod
    def _capability_cache_view(value: JsonObject, *, cached: bool, stale: bool) -> JsonObject:
        view = dict(value)
        raw_status = value.get("status")
        if isinstance(raw_status, dict):
            view["status"] = {**raw_status, "cached": cached, "stale": stale}
        return view

    async def list_threads(self, repository_id: str, session_id: str) -> tuple[JsonObject, ...]:
        summary, path = await asyncio.to_thread(
            self.repositories.resolve_repository_id, repository_id
        )
        result = await self._call(
            "thread/list",
            self.protocol.thread_list_params(str(path)),
            owner=session_id,
        )
        payload = self._object(result, "thread/list result")
        data = payload.get("data")
        if not isinstance(data, list):
            raise RuntimeUnavailableError("thread/list returned invalid data")
        projected: list[JsonObject] = []
        for value in data:
            if not isinstance(value, dict):
                continue
            raw = value
            if not self._thread_cwd_matches(raw, path):
                continue
            thread_id = raw.get("id")
            stored = (
                await asyncio.to_thread(self.metadata.get_thread, thread_id)
                if isinstance(thread_id, str)
                else None
            )
            thread = await self.projection.register_thread(
                raw,
                repository_id=summary.id,
                repository_name=summary.name,
                repository_path=path,
                repository_relative_path=summary.relative_path,
                session_id=session_id,
                sandbox_mode=stored.sandbox_mode if stored is not None else "workspace-write",
                approval_policy=stored.approval_policy if stored is not None else "on-request",
            )
            projected.append(self.projection.thread_browser_dict(thread))
        return tuple(projected)

    async def start_thread(
        self,
        *,
        repository_id: str,
        session_id: str,
        sandbox_mode: str,
        approval_policy: str,
        confirmation_id: str | None,
        full_access_scope_id: str,
    ) -> JsonObject:
        summary, path = await asyncio.to_thread(
            self.repositories.resolve_repository_id, repository_id
        )
        self._require_sandbox_available(sandbox_mode)
        self.protocol.validate_permission_selection(sandbox_mode, approval_policy)
        if sandbox_mode == "danger-full-access":
            await self.full_access.consume(
                confirmation_id,
                session_id=session_id,
                repository_id=repository_id,
                scope_id=full_access_scope_id,
                approval_policy=approval_policy,
            )
        elif confirmation_id is not None:
            raise ValueError("full access confirmation is not applicable")
        result = await self._call(
            "thread/start",
            self.protocol.thread_start_params(str(path), sandbox_mode, approval_policy),
            owner=session_id,
        )
        response = self._object(result, "thread/start result")
        raw_thread = self._object(response.get("thread"), "thread/start thread")
        if not self._thread_cwd_matches(raw_thread, path):
            raise RuntimeUnavailableError("thread/start returned an unexpected working directory")
        thread = await self.projection.register_thread(
            raw_thread,
            repository_id=summary.id,
            repository_name=summary.name,
            repository_path=path,
            repository_relative_path=summary.relative_path,
            session_id=session_id,
            sandbox_mode=sandbox_mode,
            approval_policy=approval_policy,
        )
        if sandbox_mode == "danger-full-access":
            await asyncio.to_thread(
                self.metadata.audit,
                "full_access_activated",
                occurred_at=int(time.time()),
                session_id=session_id,
                thread_id=thread.id,
                repository_id=repository_id,
            )
        browser = self.projection.thread_browser_dict(thread)
        await self.broker.publish(session_id, "thread.started", browser, thread_id=thread.id)
        return browser

    async def resume_thread(
        self,
        *,
        thread_id: str,
        repository_id: str,
        session_id: str,
        sandbox_mode: str,
        approval_policy: str,
        confirmation_id: str | None,
    ) -> JsonObject:
        summary, path = await asyncio.to_thread(
            self.repositories.resolve_repository_id, repository_id
        )
        self._require_sandbox_available(sandbox_mode)
        self.protocol.validate_permission_selection(sandbox_mode, approval_policy)
        read = await self._call(
            "thread/read", {"threadId": thread_id, "includeTurns": False}, owner=session_id
        )
        read_response = self._object(read, "thread/read result")
        candidate = self._object(read_response.get("thread"), "thread/read thread")
        if candidate.get("id") != thread_id or not self._thread_cwd_matches(candidate, path):
            raise ValueError("thread does not belong to the selected repository")
        if sandbox_mode == "danger-full-access":
            await self.full_access.consume(
                confirmation_id,
                session_id=session_id,
                repository_id=repository_id,
                scope_id=thread_id,
                approval_policy=approval_policy,
            )
        elif confirmation_id is not None:
            raise ValueError("full access confirmation is not applicable")
        result = await self._call(
            "thread/resume",
            self.protocol.thread_resume_params(thread_id, str(path), sandbox_mode, approval_policy),
            owner=session_id,
        )
        response = self._object(result, "thread/resume result")
        raw_thread = self._object(response.get("thread"), "thread/resume thread")
        if not self._thread_cwd_matches(raw_thread, path):
            raise RuntimeUnavailableError("thread/resume returned an unexpected working directory")
        thread = await self.projection.register_thread(
            raw_thread,
            repository_id=summary.id,
            repository_name=summary.name,
            repository_path=path,
            repository_relative_path=summary.relative_path,
            session_id=session_id,
            sandbox_mode=sandbox_mode,
            approval_policy=approval_policy,
        )
        if sandbox_mode == "danger-full-access":
            await asyncio.to_thread(
                self.metadata.audit,
                "full_access_activated",
                occurred_at=int(time.time()),
                session_id=session_id,
                thread_id=thread_id,
                repository_id=repository_id,
            )
        browser = self.projection.thread_browser_dict(thread)
        await self.broker.publish(session_id, "thread.resumed", browser, thread_id=thread.id)
        return browser

    async def read_thread(self, thread_id: str, session_id: str) -> JsonObject:
        try:
            thread = self.projection.require_thread(thread_id)
        except ValueError:
            reference = await asyncio.to_thread(self.metadata.get_thread, thread_id)
            if reference is None:
                raise ValueError("thread is unavailable") from None
            try:
                summary, validated_path = await asyncio.to_thread(
                    self.repositories.resolve_repository_id, reference.repository_id
                )
                repository_id = summary.id
                repository_name = summary.name
                relative_path = summary.relative_path
                repository_available = (
                    await asyncio.to_thread(
                        self.metadata.repository_registration_state, reference.repository_id
                    )
                    is None
                )
            except ValueError:
                validated_path = await asyncio.to_thread(
                    self.repositories.reference_path, reference.repository_relative_path
                )
                repository_id = reference.repository_id
                repository_name = validated_path.name
                relative_path = reference.repository_relative_path
                repository_available = False
            sandbox_mode = reference.sandbox_mode
            approval_policy = reference.approval_policy
        else:
            if thread.session_id != session_id:
                raise ValueError("thread is unavailable")
            if thread.repository_available:
                _, validated_path = await asyncio.to_thread(
                    self.repositories.resolve_repository_id, thread.repository_id
                )
                if validated_path != thread.repository_path:
                    raise ValueError("thread repository changed")
            else:
                validated_path = thread.repository_path
            repository_id = thread.repository_id
            repository_name = thread.repository_name
            relative_path = thread.repository_relative_path
            sandbox_mode = thread.sandbox_mode
            approval_policy = thread.approval_policy
            repository_available = thread.repository_available
        result = await self._call(
            "thread/read", {"threadId": thread_id, "includeTurns": True}, owner=session_id
        )
        response = self._object(result, "thread/read result")
        raw_thread = self._object(response.get("thread"), "thread/read thread")
        if not self._thread_cwd_reference_matches(raw_thread, validated_path):
            raise ValueError("thread working directory changed")
        refreshed = await self.projection.register_thread(
            raw_thread,
            repository_id=repository_id,
            repository_name=repository_name,
            repository_path=validated_path,
            repository_relative_path=relative_path,
            session_id=session_id,
            sandbox_mode=sandbox_mode,
            approval_policy=approval_policy,
            repository_available=repository_available,
        )
        return self.projection.thread_browser_dict(refreshed)

    async def delete_thread(
        self, *, thread_id: str, repository_id: str, session_id: str
    ) -> JsonObject:
        """Delete one known idle thread without hydrating it through thread/read."""
        lock = self._thread_mutations.setdefault(thread_id, asyncio.Lock())
        async with lock:
            reference = await asyncio.to_thread(self.metadata.get_thread, thread_id)
            if (
                reference is None
                or reference.repository_id != repository_id
                or reference.last_session_id != session_id
            ):
                raise ThreadUnavailableError("thread is unavailable")
            await asyncio.to_thread(
                self.repositories.resolve_repository_id, reference.repository_id
            )
            if self.projection.thread_has_active_turn(thread_id):
                raise RuntimeConflictError(
                    "Der aktive Turn muss vor dem Löschen gestoppt werden."
                )
            await self._call(
                "thread/delete", {"threadId": thread_id}, owner=session_id
            )
            await self.approvals.invalidate_turn(thread_id, None)
            await asyncio.to_thread(self.metadata.delete_thread, thread_id)
            self.projection.forget_thread(thread_id)
            self._unavailable_threads.pop(thread_id, None)
            self._reconciliation_failures.discard(thread_id)
            await asyncio.to_thread(
                self.metadata.audit,
                "thread_deleted",
                occurred_at=int(time.time()),
                session_id=session_id,
                thread_id=thread_id,
                repository_id=repository_id,
            )
            await self.broker.publish(
                session_id,
                "thread.deleted",
                {"threadId": thread_id, "repositoryId": repository_id},
                thread_id=thread_id,
            )
        self._thread_mutations.pop(thread_id, None)
        return {"deleted": True, "threadId": thread_id}

    async def start_turn(
        self,
        *,
        thread_id: str,
        session_id: str,
        text: str,
        images: tuple[tuple[str, str], ...],
        sandbox_mode: str,
        approval_policy: str,
        confirmation_id: str | None,
        idempotency_key: str,
        model: str | None,
        effort: str | None,
    ) -> JsonObject:
        lock = self._thread_mutations.setdefault(thread_id, asyncio.Lock())
        async with lock:
            thread = self.projection.require_thread(thread_id)
            if thread.session_id != session_id:
                raise ValueError("thread is unavailable")
            if thread.active_turn_id is not None or thread.status in {
                "running",
                "awaiting_approval",
            }:
                raise RuntimeConflictError("a turn is already running")
            if not thread.repository_available:
                raise RuntimeConflictError("thread repository is unavailable")
            summary, path = await asyncio.to_thread(
                self.repositories.resolve_repository_id, thread.repository_id
            )
            if path != thread.repository_path:
                raise ValueError("thread repository changed")
            self._require_sandbox_available(sandbox_mode)
            self.protocol.validate_permission_selection(sandbox_mode, approval_policy)
            activating_full_access = sandbox_mode == "danger-full-access" and (
                thread.sandbox_mode != "danger-full-access"
                or thread.approval_policy != approval_policy
            )
            if activating_full_access:
                await self.full_access.consume(
                    confirmation_id,
                    session_id=session_id,
                    repository_id=summary.id,
                    scope_id=thread_id,
                    approval_policy=approval_policy,
                )
            elif confirmation_id is not None:
                raise ValueError("full access confirmation is not applicable")
            resolved_images = await self.uploads.resolve_images(images, session_id=session_id)
            await self.projection.turn_starting(thread_id)
            try:
                result = await self._call(
                    "turn/start",
                    self.protocol.turn_start_params(
                        thread_id=thread_id,
                        cwd=str(path),
                        text=text,
                        image_paths=resolved_images,
                        sandbox_mode=sandbox_mode,
                        approval_policy=approval_policy,
                        client_message_id=idempotency_key,
                        model=model,
                        effort=effort,
                    ),
                    owner=session_id,
                )
            except BaseException:
                await self.projection.turn_start_failed(thread_id)
                raise
            response = self._object(result, "turn/start result")
            raw_turn = self._object(response.get("turn"), "turn/start turn")
            turn_id = raw_turn.get("id")
            if not isinstance(turn_id, str):
                raise RuntimeUnavailableError("turn/start returned no turn id")
            thread.active_turn_id = turn_id
            thread.status = "running"
            await self.projection.set_permission_selection(thread_id, sandbox_mode, approval_policy)
            stored = await asyncio.to_thread(self.metadata.get_thread, thread_id)
            now = int(time.time())
            await asyncio.to_thread(
                self.metadata.upsert_thread,
                ThreadReference(
                    thread_id=thread_id,
                    repository_id=thread.repository_id,
                    repository_relative_path=thread.repository_relative_path,
                    last_session_id=session_id,
                    sandbox_mode=sandbox_mode,
                    approval_policy=approval_policy,
                    created_at=stored.created_at if stored is not None else now,
                    updated_at=now,
                ),
            )
            await self.uploads.mark_consumed(
                tuple(upload_id for upload_id, _detail in images), session_id=session_id
            )
            if activating_full_access:
                await asyncio.to_thread(
                    self.metadata.audit,
                    "full_access_activated",
                    occurred_at=now,
                    session_id=session_id,
                    thread_id=thread_id,
                    repository_id=thread.repository_id,
                )
            return {"turnId": turn_id, "status": "inProgress"}

    async def interrupt_turn(self, thread_id: str, session_id: str) -> JsonObject:
        thread = self.projection.require_thread(thread_id)
        if thread.session_id != session_id:
            raise ValueError("thread is unavailable")
        turn_id = thread.active_turn_id
        if turn_id is None:
            raise RuntimeConflictError("there is no active turn")
        await self._call(
            "turn/interrupt",
            {"threadId": thread_id, "turnId": turn_id},
            owner=session_id,
        )
        await self.broker.publish(
            session_id,
            "turn.interrupt_requested",
            {"turnId": turn_id},
            thread_id=thread_id,
            turn_id=turn_id,
        )
        return {"turnId": turn_id, "status": "interrupting"}

    async def decide_approval(
        self, approval_id: str, *, session_id: str, decision: str
    ) -> JsonObject:
        generation = self.supervisor.status.generation
        try:
            claim = await self.approvals.claim(
                approval_id,
                session_id=session_id,
                generation=generation,
                decision=decision,
            )
        except ApprovalReplayError:
            raise RuntimeConflictError("approval was already answered") from None
        except ApprovalUnavailableError:
            raise ValueError("approval is unavailable") from None
        client = self.supervisor.client_for_generation(generation)
        succeeded = False
        try:
            await client.respond(claim.record.server_request_id, claim.response)
            succeeded = True
        finally:
            await self.approvals.mark_responded(approval_id, succeeded=succeeded)
        await self.projection.publish_approval_resolved(claim.record.thread_id, approval_id)
        await asyncio.to_thread(
            self.metadata.audit,
            "approval_responded",
            occurred_at=int(time.time()),
            session_id=session_id,
            thread_id=claim.record.thread_id,
        )
        return {"approvalId": approval_id, "state": "responded"}

    async def read_rate_limits(self, session_id: str) -> JsonObject:
        result = await self._call("account/rateLimits/read", None, owner=session_id)
        return await self.projection.set_rate_limits(
            session_id, self._object(result, "account/rateLimits/read result")
        )

    async def snapshot(self, session_id: str) -> JsonObject:
        pending = await self.approvals.list_pending(session_id)
        snapshot = self.projection.snapshot(
            session_id, tuple(record.browser_dict() for record in pending)
        )
        snapshot["jobs"] = list(await self.jobs.list_for_session(session_id))
        snapshot["unavailableThreads"] = [
            value
            for owner_session_id, value in self._unavailable_threads.values()
            if owner_session_id == session_id
        ]
        return snapshot

    async def invalidate_session(self, session_id: str) -> None:
        invalidated = await self.approvals.invalidate_session(session_id)
        await self.full_access.invalidate_session(session_id)
        for record in invalidated:
            await self._respond_safe(record)
            await self.projection.publish_approval_resolved(record.thread_id, record.id)

    async def _on_notification(self, generation: int, method: str, params: JsonObject) -> None:
        if generation != self.supervisor.status.generation:
            return
        if method == "skills/changed":
            async with self._capability_lock:
                self._capability_cache.clear()
        if method == "serverRequest/resolved":
            request_id = params.get("requestId")
            if isinstance(request_id, str | int) and not isinstance(request_id, bool):
                resolved = await self.approvals.resolve_native(generation, request_id)
                if resolved is not None:
                    await self.projection.publish_approval_resolved(resolved.thread_id, resolved.id)
        await self.projection.ingest_notification(generation, method, params)
        if method == "turn/completed":
            thread_id = params.get("threadId")
            turn = params.get("turn")
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if isinstance(thread_id, str):
                invalidated = await self.approvals.invalidate_turn(
                    thread_id, turn_id if isinstance(turn_id, str) else None
                )
                for record in invalidated:
                    await self.projection.publish_approval_resolved(thread_id, record.id)
                if thread_id not in self._title_refresh_tasks:
                    task = asyncio.create_task(
                        self._refresh_native_thread_title(thread_id),
                        name="thread-title-refresh",
                    )
                    self._title_refresh_tasks[thread_id] = task

                    def forget_title_refresh(_completed: asyncio.Task[None]) -> None:
                        self._title_refresh_tasks.pop(thread_id, None)

                    task.add_done_callback(forget_title_refresh)

    async def _refresh_native_thread_title(self, thread_id: str) -> None:
        """Refresh Codex's native title after a completed turn without creating a turn."""

        try:
            thread = self.projection.require_thread(thread_id)
            stored = await asyncio.to_thread(self.metadata.get_thread_title, thread_id)
            if stored is None or stored.source == "manual":
                return
            pending = await asyncio.to_thread(
                self.metadata.set_thread_title,
                thread_id,
                stored.display_title,
                source=stored.source,
                updated_at=int(time.time()),
                generation_state="pending",
                error_code=None,
            )
            await self._publish_thread_title(thread_id, thread.session_id, pending)
            result = await self._call(
                "thread/read",
                {"threadId": thread_id, "includeTurns": False},
                owner=thread.session_id,
            )
            response = self._object(result, "thread/read title refresh result")
            raw_thread = self._object(response.get("thread"), "thread/read title refresh thread")
            if raw_thread.get("id") != thread_id:
                raise ValueError("thread title refresh returned a different thread")
            raw_name = raw_thread.get("name")
            native_title = self._clean_thread_title(raw_name if isinstance(raw_name, str) else None)
            if native_title is None:
                unavailable = await asyncio.to_thread(
                    self.metadata.set_thread_title,
                    thread_id,
                    pending.display_title,
                    source=pending.source,
                    updated_at=int(time.time()),
                    generation_state="unavailable",
                    error_code="native_title_unavailable",
                )
                await self._publish_thread_title(thread_id, thread.session_id, unavailable)
                return
            resolved = await asyncio.to_thread(
                self.metadata.resolve_thread_title,
                thread_id,
                native_title=native_title,
                fallback_title=pending.display_title,
                observed_at=int(time.time()),
            )
            await self._publish_thread_title(thread_id, thread.session_id, resolved)
        except asyncio.CancelledError:
            raise
        except (AppServerRequestError, AppServerRequestTimeoutError, AppServerDisconnectedError):
            await self._mark_title_refresh_failed(thread_id, "native_title_refresh_failed")
        except (RuntimeUnavailableError, RuntimeError, ValueError):
            await self._mark_title_refresh_failed(thread_id, "native_title_refresh_invalid")

    async def _mark_title_refresh_failed(self, thread_id: str, error_code: str) -> None:
        stored = await asyncio.to_thread(self.metadata.get_thread_title, thread_id)
        if stored is None or stored.source == "manual":
            return
        failed = await asyncio.to_thread(
            self.metadata.set_thread_title,
            thread_id,
            stored.display_title,
            source=stored.source,
            updated_at=int(time.time()),
            generation_state="failed",
            error_code=error_code,
        )
        thread = self.projection.require_thread(thread_id)
        await self._publish_thread_title(thread_id, thread.session_id, failed)

    async def _publish_thread_title(
        self, thread_id: str, session_id: str, stored: ThreadTitle
    ) -> None:
        await self.projection.apply_thread_title(
            thread_id,
            display_title=stored.display_title,
            source=stored.source,
            updated_at=stored.updated_at,
            generation_state=stored.generation_state,
            error_code=stored.error_code,
        )
        await self.broker.publish(
            session_id,
            "thread.name",
            {
                "name": stored.display_title,
                "titleSource": stored.source,
                "titleUpdatedAt": stored.updated_at,
                "titleGenerationState": stored.generation_state,
                "titleErrorCode": stored.error_code,
            },
            thread_id=thread_id,
        )

    @staticmethod
    def _clean_thread_title(value: str | None) -> str | None:
        if value is None:
            return None
        title = " ".join(value.split()).strip(" \t\r\n\"'`#*_~")
        title = title.removesuffix("...").strip()
        return title[:80].rstrip() or None

    async def _on_job_updated(self, session_id: str, job: JsonObject) -> None:
        await self.broker.publish(session_id, "job.updated", job)

    async def _on_server_request(
        self,
        generation: int,
        request_id: JsonScalar,
        method: str,
        params: JsonObject,
    ) -> None:
        client = self.supervisor.client_for_generation(generation)
        thread_id = self._server_request_thread_id(method, params)
        thread = self._optional_thread(thread_id)
        if thread is not None:
            record = await self.approvals.register(
                generation=generation,
                request_id=request_id,
                method=method,
                params=params,
                session_id=thread.session_id,
                cwd_display=thread.repository_name,
            )
            if record is not None:
                self._approval_expiry_tasks[record.id] = asyncio.create_task(
                    self._expire_approval(record),
                    name=f"approval-expiry-{record.id[-8:]}",
                )
                await self.projection.publish_approval(thread.id, record.browser_dict())
                return
        safe_response = self._safe_server_request_response(method)
        if safe_response is None:
            await client.respond_error(request_id)
        else:
            await client.respond(request_id, safe_response)

    async def _on_disconnect(self, generation: int, exc: BaseException) -> None:
        for capability in tuple(self._capability_transport_probes.get(generation, ())):
            self._open_capability_circuit(capability, generation)
        invalidated = await self.approvals.invalidate_generation(generation)
        for record in invalidated:
            await self.projection.publish_approval_resolved(record.thread_id, record.id)
        await self.projection.connection_state("reconnecting", generation, type(exc).__name__)

    async def _on_ready(self, generation: int) -> None:
        await self.projection.connection_state("connected", generation)
        if generation > 1:
            await self._reconcile()
        session_id = self._manual_restart_session_id
        if session_id is not None:
            self._manual_restart_session_id = None
            await asyncio.to_thread(
                self.metadata.audit,
                "app_server_restart_completed",
                occurred_at=int(time.time()),
                session_id=session_id,
            )
            logger.info(
                "manual_runtime_restart_completed",
                extra={
                    "component": "codex_app_server",
                    "generation": generation,
                    "state": "healthy",
                    "succeeded": True,
                },
            )

    async def _on_terminal_failure(self, generation: int, error_code: str) -> None:
        await self.projection.connection_state("failed", generation, error_code)
        if self._manual_restart_session_id is not None:
            self._manual_restart_session_id = None
            logger.warning(
                "manual_runtime_restart_completed",
                extra={
                    "component": "codex_app_server",
                    "generation": generation,
                    "error_code": error_code,
                    "state": "failed",
                    "succeeded": False,
                },
            )

    async def _expire_approval(self, record: ApprovalRecord) -> None:
        try:
            delay = max(0.0, record.expires_at - time.time())
            await asyncio.sleep(delay)
            expired = await self.approvals.expire(record.id)
            if expired is None:
                return
            await self._respond_safe(expired)
            await self.projection.publish_approval_resolved(expired.thread_id, expired.id)
            await asyncio.to_thread(
                self.metadata.audit,
                "approval_expired",
                occurred_at=int(time.time()),
                session_id=expired.session_id,
                thread_id=expired.thread_id,
            )
        finally:
            self._approval_expiry_tasks.pop(record.id, None)

    async def _respond_safe(self, record: ApprovalRecord) -> None:
        if record.generation != self.supervisor.status.generation:
            return
        try:
            client = self.supervisor.client_for_generation(record.generation)
            response = self._safe_server_request_response(record.method)
            if response is None:
                await client.respond_error(record.server_request_id)
            else:
                await client.respond(record.server_request_id, response)
        except RuntimeError:
            return

    async def _reconcile(self) -> None:
        references = await asyncio.to_thread(self.metadata.list_threads)
        for reference in references:
            try:
                try:
                    summary, path = await asyncio.to_thread(
                        self.repositories.resolve_repository_id, reference.repository_id
                    )
                    repository_name = summary.name
                    relative_path = summary.relative_path
                except ValueError:
                    path = await asyncio.to_thread(
                        self.repositories.reference_path, reference.repository_relative_path
                    )
                    repository_name = path.name
                    relative_path = reference.repository_relative_path
                repository_available = (
                    await asyncio.to_thread(
                        self.metadata.repository_registration_state, reference.repository_id
                    )
                    is None
                )
                result = await self._call(
                    "thread/read",
                    {"threadId": reference.thread_id, "includeTurns": True},
                    owner=reference.last_session_id,
                )
                response = self._object(result, "thread/read reconciliation")
                raw = self._object(response.get("thread"), "thread/read thread")
                if not self._thread_cwd_reference_matches(raw, path):
                    raise ValueError("thread cwd no longer matches its repository")
                thread = await self.projection.register_thread(
                    raw,
                    repository_id=reference.repository_id,
                    repository_name=repository_name,
                    repository_path=path,
                    repository_relative_path=relative_path,
                    session_id=reference.last_session_id,
                    sandbox_mode=reference.sandbox_mode,
                    approval_policy=reference.approval_policy,
                    repository_available=repository_available,
                )
                await self.broker.publish(
                    thread.session_id,
                    "thread.reconciled",
                    self.projection.thread_browser_dict(thread),
                    thread_id=thread.id,
                )
                self._reconciliation_failures.discard(reference.thread_id)
                self._unavailable_threads.pop(reference.thread_id, None)
            except (AppServerDisconnectedError, RuntimeUnavailableError):
                logger.warning("thread_reconciliation_interrupted")
                break
            except (AppServerRequestError, RuntimeError, ValueError) as exc:
                unavailable: JsonObject = {
                    "id": reference.thread_id,
                    "repositoryId": reference.repository_id,
                    "status": "unavailable",
                    "reason": "reconciliation_failed",
                }
                self._unavailable_threads[reference.thread_id] = (
                    reference.last_session_id,
                    unavailable,
                )
                await self.broker.publish(
                    reference.last_session_id,
                    "thread.unavailable",
                    unavailable,
                    thread_id=reference.thread_id,
                )
                if reference.thread_id not in self._reconciliation_failures:
                    logger.warning(
                        "thread_reconciliation_failed",
                        extra={
                            "thread_id": reference.thread_id,
                            "error_type": type(exc).__name__,
                        },
                    )
                    self._reconciliation_failures.add(reference.thread_id)

    async def _call(self, method: str, params: JsonObject | None, *, owner: str) -> JsonValue:
        if not self.settings.app_server_start_enabled:
            raise RuntimeUnavailableError("app-server startup is disabled")
        if not self.app_server_ready:
            raise RuntimeUnavailableError("app-server is not ready")
        generation = self.supervisor.status.generation
        try:
            return await self.supervisor.client.call(method, params, owner=owner)
        except AppServerRequestError as exc:
            logger.warning(
                "app_server_request_error",
                extra={
                    "method": method,
                    "error_code": exc.code,
                    "generation": generation,
                },
            )
            raise

    async def _call_for_generation(
        self,
        generation: int,
        method: str,
        params: JsonObject | None,
        *,
        owner: str,
    ) -> JsonValue:
        status = self.supervisor.status
        if status.generation != generation or status.state is not ProcessState.READY:
            raise RuntimeUnavailableError("app-server generation is not ready")
        try:
            client = self.supervisor.client_for_generation(generation)
        except RuntimeError as exc:
            raise RuntimeUnavailableError("app-server generation is not ready") from exc
        return await client.call(method, params, owner=owner)

    @staticmethod
    def _object(value: JsonValue | None, label: str) -> JsonObject:
        if not isinstance(value, dict):
            raise RuntimeUnavailableError(f"{label} is invalid")
        return value

    @staticmethod
    def _json_object(value: dict[str, object]) -> JsonObject:
        # GitService returns browser primitives only; this boundary keeps transport typing strict.
        return cast(JsonObject, value)

    @staticmethod
    def _array(value: JsonValue | None) -> list[JsonValue]:
        return value if isinstance(value, list) else []

    @staticmethod
    def _safe_string(value: JsonValue | None) -> str:
        return value if isinstance(value, str) else ""

    def _require_sandbox_available(self, sandbox_mode: str) -> None:
        key = {
            "workspace-write": "workspaceWrite",
            "read-only": "readOnly",
            "danger-full-access": "fullAccess",
        }.get(sandbox_mode)
        if key is None:
            return
        sandbox = self.host_capabilities.get("sandbox")
        capability = sandbox.get(key) if isinstance(sandbox, dict) else None
        if isinstance(capability, dict) and capability.get("available") is False:
            raise ValueError("sandbox mode is unavailable on this host")

    @staticmethod
    def _thread_cwd_matches(thread: JsonObject, repository_path: Path) -> bool:
        cwd = thread.get("cwd")
        if not isinstance(cwd, str):
            return False
        try:
            return Path(cwd).resolve(strict=True) == repository_path.resolve(strict=True)
        except (OSError, RuntimeError):
            return False

    @staticmethod
    def _thread_cwd_reference_matches(thread: JsonObject, repository_path: Path) -> bool:
        cwd = thread.get("cwd")
        if not isinstance(cwd, str):
            return False
        try:
            return Path(cwd).resolve(strict=False) == repository_path.resolve(strict=False)
        except (OSError, RuntimeError):
            return False

    @staticmethod
    def _server_request_thread_id(method: str, params: JsonObject) -> str | None:
        field = (
            "conversationId"
            if method in {"execCommandApproval", "applyPatchApproval"}
            else "threadId"
        )
        value = params.get(field)
        return value if isinstance(value, str) else None

    def _optional_thread(self, thread_id: str | None) -> ProjectedThread | None:
        if thread_id is None:
            return None
        try:
            return self.projection.require_thread(thread_id)
        except ValueError:
            return None

    @staticmethod
    def _safe_server_request_response(method: str) -> JsonObject | None:
        if method == "item/commandExecution/requestApproval":
            return {"decision": "decline"}
        if method == "item/fileChange/requestApproval":
            return {"decision": "decline"}
        if method == "item/permissions/requestApproval":
            return {"permissions": {}, "scope": "turn", "strictAutoReview": False}
        if method in {"execCommandApproval", "applyPatchApproval"}:
            return {"decision": {"denied": {"rejection": "Unavailable in Workbench"}}}
        if method == "item/tool/requestUserInput":
            return {"answers": {}}
        if method == "mcpServer/elicitation/request":
            return {"action": "decline", "content": None, "_meta": None}
        if method == "item/tool/call":
            return {"contentItems": [], "success": False}
        return None
