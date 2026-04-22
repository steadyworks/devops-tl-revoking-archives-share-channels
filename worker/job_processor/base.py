from abc import ABC, abstractmethod
from typing import Generic, TypeVar
from uuid import UUID

from db.session.factory import AsyncSessionFactory
from lib.asset_manager.base import AssetManager
from worker.process.types import WorkerProcessResources

from .types import JobInputPayload, JobOutputPayload

TInputPayload = TypeVar("TInputPayload", bound=JobInputPayload, contravariant=True)
TOutputPayload = TypeVar("TOutputPayload", bound=JobOutputPayload, covariant=True)
TWorkerProcessResources = TypeVar(
    "TWorkerProcessResources",
    bound=WorkerProcessResources,
    covariant=True,
)


class AbstractJobProcessor(
    Generic[TInputPayload, TOutputPayload, TWorkerProcessResources], ABC
):
    def __init__(
        self,
        job_id: UUID,
        asset_manager: AssetManager,
        db_session_factory: AsyncSessionFactory,
        worker_process_resources: TWorkerProcessResources,
    ) -> None:
        self.job_id = job_id
        self.asset_manager = asset_manager
        self.db_session_factory = db_session_factory
        self.worker_process_resources = worker_process_resources

    @abstractmethod
    async def process(self, input_payload: TInputPayload) -> TOutputPayload: ...
