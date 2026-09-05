# noinspection PyUnresolvedReferences
from .defaults import *

MIDDLEWARE += (
    'mutint_common.middleware.LoginRequiredMiddleware',
)
