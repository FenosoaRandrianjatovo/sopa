import logging

from ..aggregation import Aggregator as _Aggregator

log = logging.getLogger(__name__)


def overlay_segmentation(*args, **kwargs):
    from .. import overlay_segmentation as _overlay_segmentation

    log.warning(
        "overlay_segmentation is deprecated, use `sopa.overlay_segmentation` instead. See our migration guide to sopa 2.0.0: https://github.com/gustaveroussy/sopa/discussions/138"
    )
    _overlay_segmentation(*args, **kwargs)


class Aggregator(_Aggregator):
    def __init__(self, *args, **kwargs):
        log.warning(
            "Aggregator is deprecated, use `sopa.aggregate` instead. See our migration guide to sopa 2.0.0: https://github.com/gustaveroussy/sopa/discussions/138"
        )
        super().__init__(*args, **kwargs)
