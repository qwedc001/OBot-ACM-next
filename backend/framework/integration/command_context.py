"""Application dependencies, authorization and metrics in native matcher hooks."""

import time
from dataclasses import dataclass, field, replace
from typing import Annotated
from uuid import uuid4

from nonebot.adapters import Bot, Event
from nonebot.dependencies import Dependent
from nonebot.log import logger
from nonebot.matcher import Matcher, current_matcher
from nonebot.message import run_postprocessor, run_preprocessor
from nonebot.params import Depends
from nonebot.utils import flatten_exception_group

from ..catalog import CommandSpec
from ..clients.reply import DeliveryFailed, ReplyClient
from ..contracts import AccessDenied, RequestContext
from ..runtime import Services, get_services
from ..telemetry import CommandRun
from .permissions import command_check
from .platforms import make_context

_STATE = "_framework_command_execution"
_SPEC = "_framework_command_spec"


@dataclass
class _Execution:
    spec: CommandSpec
    started: float = field(default_factory=time.perf_counter)
    correlation_id: str = field(default_factory=lambda: uuid4().hex)
    context: RequestContext | None = None
    context_ms: float = 0
    deliveries: list[tuple[float, float]] = field(default_factory=list)
    outcome: str = "success"


async def prepare(bot: Bot, event: Event, matcher: Matcher):
    execution = _Execution(matcher.state[_SPEC])
    matcher.state[_STATE] = execution
    services = get_services()
    execution.context = replace(
        await make_context(services, bot, event),
        command_allowed=command_check(bot, event),
    )
    execution.correlation_id = execution.context.correlation_id
    allowed = await execution.context.command_allowed(execution.spec)
    execution.context_ms = (time.perf_counter() - execution.started) * 1000
    if not allowed:
        execution.outcome = "denied"
        await services.clients.reply.finish("权限不足，操作被拒绝。")


_prepare_handler = Dependent.parse(call=prepare, allow_types=Matcher.HANDLER_PARAM_TYPES)


@run_preprocessor
async def attach_context(matcher: Matcher):
    # Session continuations created by pause/reject/got inherit native state.
    # Attach the same first handler each turn so identity/permissions stay current.
    if isinstance(matcher.state.get(_SPEC), CommandSpec):
        matcher.remain_handlers.insert(0, _prepare_handler)


def command_context(matcher: Matcher) -> RequestContext:
    execution = matcher.state.get(_STATE)
    if not isinstance(execution, _Execution) or execution.context is None:
        raise RuntimeError("Command context requires a framework-registered matcher")
    return execution.context


def reply_client() -> ReplyClient:
    return get_services().clients.reply


CommandContext = Annotated[RequestContext, Depends(command_context)]
BotServices = Annotated[Services, Depends(get_services)]
Reply = Annotated[ReplyClient, Depends(reply_client)]


def observe_delivery(started: float, finished: float, delivered: bool) -> None:
    """Optional client observer installed by integration, without handler interception."""
    services = get_services()
    if delivered:
        services.telemetry.last_sent_at = time.time()
    try:
        execution = current_matcher.get().state.get(_STATE)
    except LookupError:
        return
    if isinstance(execution, _Execution):
        execution.deliveries.append((started, finished))
        if not delivered:
            execution.outcome = "delivery_failed"


def _delivery_seconds(intervals: list[tuple[float, float]]) -> float:
    # Parallel sends overlap: measure their union instead of double-counting time.
    total, end = 0.0, 0.0
    for start, finish in sorted(intervals):
        total += max(0.0, finish - max(start, end))
        end = max(end, finish)
    return total


@run_postprocessor
async def completed(matcher: Matcher, exception: Exception | None):
    execution = matcher.state.get(_STATE)
    if not isinstance(execution, _Execution):
        return
    services = get_services()
    if exception is not None:
        errors = (
            list(flatten_exception_group(exception))
            if isinstance(exception, BaseExceptionGroup)
            else [exception]
        )
        denied = all(isinstance(error, AccessDenied) for error in errors)
        if all(isinstance(error, DeliveryFailed) for error in errors):
            execution.outcome = "delivery_failed"
        else:
            execution.outcome = "denied" if denied else "failed"
            logger.error(
                "OBot command failed: {} {} {}",
                execution.spec.id,
                execution.correlation_id,
                type(exception).__name__,
            )
            try:
                await services.clients.reply.send(
                    "权限不足，操作被拒绝。"
                    if denied
                    else f"处理失败，请稍后重试。参考号：{execution.correlation_id[:8]}"
                )
            except Exception:
                execution.outcome = "delivery_failed"
    duration_ms = (time.perf_counter() - execution.started) * 1000
    delivery_ms = _delivery_seconds(execution.deliveries) * 1000
    handling_ms = max(0.0, duration_ms - execution.context_ms - delivery_ms)
    services.telemetry.record(
        CommandRun(
            execution.spec.id,
            execution.correlation_id,
            execution.outcome,
            round(duration_ms, 2),
            round(execution.context_ms, 2),
            round(handling_ms, 2),
            round(delivery_ms, 2),
        )
    )
    logger.info(
        "OBot latency: command={} outcome={} context_ms={:.2f} handling_ms={:.2f} delivery_ms={:.2f} total_ms={:.2f}",
        execution.spec.id,
        execution.outcome,
        execution.context_ms,
        handling_ms,
        delivery_ms,
        duration_ms,
    )
