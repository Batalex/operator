### This file contains stuff that ideally should be in ops.
# see https://github.com/canonical/operator/pull/862

import inspect
import os
from typing import TYPE_CHECKING, Callable, Literal, Optional

import ops.charm
import ops.framework
import ops.model
import ops.storage
from ops.charm import CharmMeta
from ops.log import setup_root_logging
from ops.main import CHARM_STATE_FILE, _Dispatcher, _emit_charm_event, _get_charm_dir

from scenario.logger import logger as scenario_logger
from scenario.mocking import _MockModelBackend

if TYPE_CHECKING:
    from ops.testing import CharmType

    from scenario.state import Event, State, _CharmSpec

logger = scenario_logger.getChild("ops_main_mock")

OnNoEventHandler = Literal["raise", "warn", "pass"]


def main(
    pre_event: Optional[Callable[["CharmType"], None]] = None,
    post_event: Optional[Callable[["CharmType"], None]] = None,
    state: "State" = None,
    event: "Event" = None,
    charm_spec: "_CharmSpec" = None,
    on_no_event_handler: OnNoEventHandler = "raise",
):
    """Set up the charm and dispatch the observed event."""
    charm_class = charm_spec.charm_type
    charm_dir = _get_charm_dir()
    model_backend = _MockModelBackend(  # pyright: reportPrivateUsage=false
        state=state, event=event, charm_spec=charm_spec
    )
    debug = "JUJU_DEBUG" in os.environ
    setup_root_logging(model_backend, debug=debug)
    logger.debug(
        "Operator Framework %s up and running.", ops.__version__
    )  # type:ignore

    dispatcher = _Dispatcher(charm_dir)
    dispatcher.run_any_legacy_hook()

    metadata = (charm_dir / "metadata.yaml").read_text()
    actions_meta = charm_dir / "actions.yaml"
    if actions_meta.exists():
        actions_metadata = actions_meta.read_text()
    else:
        actions_metadata = None

    meta = CharmMeta.from_yaml(metadata, actions_metadata)
    model = ops.model.Model(meta, model_backend)

    charm_state_path = charm_dir / CHARM_STATE_FILE

    # TODO: add use_juju_for_storage support
    store = ops.storage.SQLiteStorage(charm_state_path)
    framework = ops.framework.Framework(store, charm_dir, meta, model)
    framework.set_breakpointhook()
    try:
        sig = inspect.signature(charm_class)
        sig.bind(framework)  # signature check

        charm = charm_class(framework)
        dispatcher.ensure_event_links(charm)

        # Skip reemission of deferred events for collect-metrics events because
        # they do not have the full access to all hook tools.
        if not dispatcher.is_restricted_context():
            framework.reemit()

        if pre_event:
            pre_event(charm)

        if not getattr(charm.on, dispatcher.event_name, None):
            if on_no_event_handler == "raise":
                raise RuntimeError(
                    f"Charm has no registered observers for {dispatcher.event_name!r}. "
                    f"This is probably not what you were looking for."
                    f"You can pass `trigger(..., on_no_event_handler='ignore'|'pass')` "
                    f"to suppress this exception if you know what you're doing."
                )
            elif on_no_event_handler == "warn":
                logger.warning(
                    f"Charm has no registered observers for {dispatcher.event_name!r}. "
                    f"This is probably not what you were looking for."
                    f"You can pass `trigger(..., on_no_event_handler='pass')` "
                    f"to suppress this warning if you know what you're doing."
                )
            elif on_no_event_handler != "pass":
                raise ValueError(
                    f"Bad on_no_event_handler value: {on_no_event_handler!r} "
                    f"(expected one of ['raise', 'warn', 'pass'])"
                )

        _emit_charm_event(charm, dispatcher.event_name)

        if post_event:
            post_event(charm)

        framework.commit()
    finally:
        framework.close()
