"""Application orchestration across transport, protocol, domain, and persistence boundaries."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from codex_workbench.app_server.correlation import (
    AppServerDisconnectedError,
    AppServerRequestError,
    AppServerRequestTimeoutError,
)
from codex_workbench.app_server.protocol import ProtocolAdapter
from codex_workbench.app_server.supervisor import AppServerSupervisor, ProcessState
from codex_workbench.app_server.transport import JsonObject, JsonScalar, JsonValue
from codex_workbench.config import Settings
from codex_workbench.domain.approvals import (
    ApprovalRecord,
    ApprovalRegistry,
    ApprovalReplayError,
    ApprovalUnavailableError,
)
from codex_workbench.domain.capabilities import FullAccessRegistry
from codex_workbench.domain.projection import DomainProjection, ProjectedThread
from codex_workbench.git_service import GitService
from codex_workbench.host_capabilities import detect_sandbox_capabilities
from codex_workbench.jobs import BackgroundJobRegistry
from codex_workbench.persistence.metadata import MetadataRepository, ThreadReference
from codex_workbench.realtime.broker import RealtimeBroker
from codex_workbench.repositories import RepositoryDiscovery, RepositorySummary
from codex_workbench.security.rate_limit import LoginRateLimiter, RateLimitDecision
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


class WorkbenchRuntime:
    def __init__(self, settings: Settings, metadata: MetadataRepository) -> None:
        self.settings = settings
        self.metadata = metadata
        self.repositories = RepositoryDiscovery(settings.repositories_root)
        self.git = GitService(
            self.repositories,
            settings.repositories_root,
            output_limit=settings.max_command_output_bytes,
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

    @property
    def app_server_ready(self) -> bool:
        return self.supervisor.status.state is ProcessState.READY

    async def start(self) -> None:
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

    async def capabilities(
        self,
        repository_id: str | None,
        session_id: str,
        *,
        refresh: bool = False,
    ) -> JsonObject:
        """Return cached, fail-soft, schema-backed browser capability metadata."""
        cwd: str | None = None
        if repository_id is not None:
            _summary, path = await asyncio.to_thread(
                self.repositories.resolve_repository_id, repository_id
            )
            cwd = str(path)
        key = repository_id or "__global__"
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
                    self._discover_capabilities(key, cwd, session_id, generation),
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
        self, key: str, cwd: str | None, session_id: str, generation: int
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
        skill_reason: str | None = None
        plugin_reason: str | None = None
        if cwd is not None:
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
        available = sum(
            1
            for value in states.values()
            if isinstance(value, dict) and value.get("available") is True
        )
        overall = "ready" if available == 3 else "unavailable" if available == 0 else "partial"
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
        return {"available": reason is None, "reason": reason}

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

    async def _on_terminal_failure(self, generation: int, error_code: str) -> None:
        await self.projection.connection_state("failed", generation, error_code)

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
