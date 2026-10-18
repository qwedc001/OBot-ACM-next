from typing import Annotated

from fastapi import Depends

from framework.curation.contestlist.store import ContestStore
from web.content.deps import Content


def get_store(service: Content) -> ContestStore:
    return service.contestlist


Store = Annotated[ContestStore, Depends(get_store)]
