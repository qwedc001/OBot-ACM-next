from typing import Annotated

from fastapi import Depends

from framework.curation.pickone.store import PickOneStore
from web.content.deps import Content


def get_store(service: Content) -> PickOneStore:
    return service.pickone


Store = Annotated[PickOneStore, Depends(get_store)]
