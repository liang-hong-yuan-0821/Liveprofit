"""WatchlistService（§3.1.3：自选分组与标的新增、排序、删除；只操作自选聚合）。"""

from __future__ import annotations

import uuid
from datetime import datetime

from backend.modules.investment_workspace.application.contracts import (
    WatchlistDTO,
    WatchlistItemDTO,
    WatchlistItemMutationResult,
)
from backend.modules.investment_workspace.application.errors import (
    InstrumentInvalidError,
    RevisionConflictError,
    WatchlistItemDuplicateError,
    WatchlistItemOrderConflictError,
    WatchlistNameConflictError,
    WatchlistNotEmptyError,
    WatchlistNotFoundError,
)
from backend.modules.investment_workspace.domain.values import InstrumentRef, is_valid_instrument
from sqlalchemy.exc import IntegrityError

from backend.modules.investment_workspace.infrastructure.models import Watchlist, WatchlistItem
from backend.modules.investment_workspace.infrastructure.repositories import (
    WatchlistItemRepository,
    WatchlistRepository,
)
from backend.shared.clock import Clock, SystemClock
from backend.shared.ids import new_uuid


class WatchlistService:
    def __init__(
        self,
        uow,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._uow = uow
        self._repo = WatchlistRepository(uow.session)
        self._items = WatchlistItemRepository(uow.session)
        self._clock = clock or SystemClock()

    # ---- 分组 ----

    def create(self, name: str) -> WatchlistDTO:
        if self._repo.get_by_name(name) is not None:
            raise WatchlistNameConflictError(f"分组名已存在：{name}")
        now = self._clock.now()
        watchlist = Watchlist(id=new_uuid(), name=name, version=1, created_at=now, updated_at=now)
        self._repo.add(watchlist)
        try:
            self._uow.commit()
        except IntegrityError:
            # 预检通过后并发撞唯一约束：映射契约 409，绝不 500
            self._uow.rollback()
            raise WatchlistNameConflictError(f"分组名已存在：{name}") from None
        return self._to_watchlist_dto(watchlist, 0)

    def list(self, *, limit: int, before: tuple[datetime, uuid.UUID] | None = None) -> tuple[list[WatchlistDTO], tuple[datetime, uuid.UUID] | None]:
        rows = self._repo.list_ordered(limit=limit + 1, before=before)
        items = [self._to_watchlist_dto(w, self._repo.count_items(w.id)) for w in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = (last.updated_at, last.id)
        return items, next_cursor

    def get(self, watchlist_id: uuid.UUID) -> WatchlistDTO:
        watchlist = self._repo.get(watchlist_id)
        if watchlist is None:
            raise WatchlistNotFoundError(f"分组不存在：{watchlist_id}")
        return self._to_watchlist_dto(watchlist, self._repo.count_items(watchlist_id))

    def rename(self, watchlist_id: uuid.UUID, name: str, expected_version: int) -> WatchlistDTO:
        watchlist = self._repo.get(watchlist_id)
        if watchlist is None:
            raise WatchlistNotFoundError(f"分组不存在：{watchlist_id}")
        if self._repo.get_by_name(name) is not None and name != watchlist.name:
            raise WatchlistNameConflictError(f"分组名已存在：{name}")
        now = self._clock.now()
        if not self._repo.conditional_update_version(
            watchlist_id, expected_version, {"name": name, "version": expected_version + 1, "updated_at": now}
        ):
            raise RevisionConflictError("分组已变更，请重新拉取")
        try:
            self._uow.commit()
        except IntegrityError:
            self._uow.rollback()
            raise WatchlistNameConflictError(f"分组名已存在：{name}") from None
        return self.get(watchlist_id)

    def delete(self, watchlist_id: uuid.UUID, expected_version: int) -> None:
        if self._repo.get(watchlist_id) is None:
            raise WatchlistNotFoundError(f"分组不存在：{watchlist_id}")
        if self._repo.count_items(watchlist_id) > 0:
            raise WatchlistNotEmptyError("仅允许删除空分组")
        if not self._repo.conditional_delete(watchlist_id, expected_version):
            raise RevisionConflictError("分组已变更，请重新拉取")
        self._uow.commit()

    # ---- 标的 ----

    def list_items(self, watchlist_id: uuid.UUID) -> tuple[list[WatchlistItemDTO], int]:
        watchlist = self._repo.get(watchlist_id)
        if watchlist is None:
            raise WatchlistNotFoundError(f"分组不存在：{watchlist_id}")
        rows = self._items.list_ordered(watchlist_id)
        return [self._to_item_dto(item) for item in rows], watchlist.version

    def add_item(self, watchlist_id: uuid.UUID, market: str, symbol: str, expected_revision: int) -> WatchlistItemMutationResult:
        if not is_valid_instrument(InstrumentRef(market=market, symbol=symbol)):
            raise InstrumentInvalidError(f"非法 market/symbol：{market} {symbol}")
        watchlist = self._repo.get(watchlist_id)
        if watchlist is None:
            raise WatchlistNotFoundError(f"分组不存在：{watchlist_id}")
        if self._items.get_by_instrument(watchlist_id, market, symbol) is not None:
            raise WatchlistItemDuplicateError("同分组同标的已存在")
        now = self._clock.now()
        if not self._repo.conditional_update_version(
            watchlist_id, expected_revision, {"version": expected_revision + 1, "updated_at": now}
        ):
            raise WatchlistItemOrderConflictError("分组已变更，请重新读取该分组标的")
        current_max = max((item.display_order for item in self._items.list_ordered(watchlist_id)), default=-1)
        item = WatchlistItem(
            id=new_uuid(),
            watchlist_id=watchlist_id,
            market=market,
            symbol=symbol,
            display_order=current_max + 1,
            created_at=now,
            updated_at=now,
        )
        self._items.add(item)
        try:
            self._uow.commit()
        except IntegrityError:
            # 预检通过后并发撞 (watchlist_id, market, symbol) 唯一约束
            self._uow.rollback()
            raise WatchlistItemDuplicateError("同分组同标的已存在") from None
        return WatchlistItemMutationResult(item=self._to_item_dto(item), watchlist_revision=expected_revision + 1)

    def reorder(
        self,
        watchlist_id: uuid.UUID,
        expected_revision: int,
        ordered: list[InstrumentRef],
    ) -> tuple[list[WatchlistItemDTO], int]:
        """批量排序：单一事务校验 revision 与条目集合后整体重排（§2.6.3 D-08）。"""
        watchlist = self._repo.get(watchlist_id)
        if watchlist is None:
            raise WatchlistNotFoundError(f"分组不存在：{watchlist_id}")
        existing = self._items.list_ordered(watchlist_id)
        existing_set = {(item.market, item.symbol) for item in existing}
        incoming_set = {(ref.market, ref.symbol) for ref in ordered}
        if incoming_set != existing_set or len(incoming_set) != len(ordered):
            raise WatchlistItemOrderConflictError("排序条目集合与当前分组不一致，请重新读取")
        now = self._clock.now()
        if not self._repo.conditional_update_version(
            watchlist_id, expected_revision, {"version": expected_revision + 1, "updated_at": now}
        ):
            raise WatchlistItemOrderConflictError("分组已变更，请重新读取该分组标的")
        position = {(ref.market, ref.symbol): index for index, ref in enumerate(ordered)}
        for item in existing:
            item.display_order = position[(item.market, item.symbol)]
            item.updated_at = now
        self._uow.commit()
        return [self._to_item_dto(item) for item in self._items.list_ordered(watchlist_id)], expected_revision + 1

    def remove_item(self, watchlist_id: uuid.UUID, item_id: uuid.UUID, expected_revision: int) -> None:
        watchlist = self._repo.get(watchlist_id)
        if watchlist is None:
            raise WatchlistNotFoundError(f"分组不存在：{watchlist_id}")
        item = self._items.get(item_id)
        if item is None or item.watchlist_id != watchlist_id:
            raise WatchlistNotFoundError(f"标的不存在：{item_id}")
        now = self._clock.now()
        if not self._repo.conditional_update_version(
            watchlist_id, expected_revision, {"version": expected_revision + 1, "updated_at": now}
        ):
            raise WatchlistItemOrderConflictError("分组已变更，请重新读取该分组标的")
        self._items.delete_by_id(item_id)
        self._uow.commit()

    # ---- 投影 ----

    @staticmethod
    def _to_watchlist_dto(watchlist: Watchlist, item_count: int) -> WatchlistDTO:
        return WatchlistDTO(
            id=watchlist.id,
            name=watchlist.name,
            version=watchlist.version,
            item_count=item_count,
            created_at=watchlist.created_at,
            updated_at=watchlist.updated_at,
        )

    @staticmethod
    def _to_item_dto(item: WatchlistItem) -> WatchlistItemDTO:
        return WatchlistItemDTO(
            id=item.id,
            watchlist_id=item.watchlist_id,
            market=item.market,
            symbol=item.symbol,
            display_order=item.display_order,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )
