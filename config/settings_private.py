# noinspection PyUnresolvedReferences
from .defaults import *

MIDDLEWARE += (
    'aledb_common.middleware.LoginRequiredMiddleware',
)
