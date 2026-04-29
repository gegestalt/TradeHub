"""Deterministic per-competition order queue.

Replaces the per-player asyncio.Lock with a per-competition asyncio.Queue
consumed by a single worker task. This guarantees:

  1. Within a competition all orders are processed strictly in arrival order.
  2. No two fills ever run concurrently in the same competition, eliminating
     the StaleDataError / negative-balance race conditions that required
     optimistic locking workarounds.
  3. The HTTP handler still receives a synchronous response — it awaits an
     asyncio.Future that the consumer resolves when the order is done.

Architecture
------------
                  HTTP request
                      |
               submit(OrderRequest)  ←─── creates a Future
                      |
               asyncio.Queue  ←──────────────────────┐
                      |                               |
               _consumer task (one per competition)   |
                      |                               |
               execute_fill / place_order             |
                      |                               |
               Future.set_result(order)  ─────────────┘
                      |
               HTTP response returned

The queue is created lazily on first use and torn down when the competition
ends. Competition IDs map to (Queue, Task) pairs in _queues.
"""

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from data_adapters.base import DataAdapter
from models.competition import Competition
from models.order import Order
from models.player import Player
from schemas.order import OrderCreate

_log = logging.getLogger(__name__)

# Registry: competition_id → (queue, consumer_task)
_queues: dict[str, tuple[asyncio.Queue, asyncio.Task]] = {}


@dataclass
class _OrderJob:
    db: AsyncSession
    player: Player
    competition: Competition
    data: OrderCreate
    adapter: DataAdapter
    future: asyncio.Future


async def _consumer(competition_id: str, queue: asyncio.Queue) -> None:
    """Single worker that drains the queue for one competition."""
    from services.order_engine import place_order as _place

    _log.debug("OrderQueue consumer started for competition %s", competition_id)
    while True:
        job: _OrderJob | None = await queue.get()
        if job is None:          # sentinel — shut down
            queue.task_done()
            break
        try:
            order = await _place(job.db, job.player, job.competition, job.data, job.adapter)
            job.future.set_result(order)
        except Exception as exc:
            job.future.set_exception(exc)
        finally:
            queue.task_done()

    _log.debug("OrderQueue consumer stopped for competition %s", competition_id)


def _get_or_create(competition_id: str) -> asyncio.Queue:
    """Return the queue for a competition, creating it if it doesn't exist."""
    if competition_id not in _queues:
        q: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(
            _consumer(competition_id, q),
            name=f"order-consumer-{competition_id}",
        )
        _queues[competition_id] = (q, task)
        _log.info("OrderQueue created for competition %s", competition_id)
    return _queues[competition_id][0]


async def submit(
    db: AsyncSession,
    player: Player,
    competition: Competition,
    data: OrderCreate,
    adapter: DataAdapter,
) -> Order:
    """Enqueue an order and await its result.

    The caller blocks until the consumer has processed the order, so the
    HTTP response is still synchronous from the client's perspective.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    queue = _get_or_create(competition.id)
    await queue.put(_OrderJob(db, player, competition, data, adapter, future))
    return await future


async def drain(competition_id: str) -> None:
    """Gracefully shut down the consumer for an ended competition.

    Sends a sentinel None to the queue so the worker exits after finishing
    the current batch, then awaits the task and removes it from the registry.
    """
    if competition_id not in _queues:
        return
    q, task = _queues.pop(competition_id)
    await q.put(None)      # sentinel
    try:
        await asyncio.wait_for(task, timeout=10)
    except asyncio.TimeoutError:
        task.cancel()
    _log.info("OrderQueue drained for competition %s", competition_id)


def queue_depth(competition_id: str) -> int:
    """Return the current number of pending orders waiting to be processed."""
    if competition_id not in _queues:
        return 0
    return _queues[competition_id][0].qsize()


def active_competitions() -> list[str]:
    """Return IDs of competitions that currently have a live consumer."""
    return list(_queues.keys())
