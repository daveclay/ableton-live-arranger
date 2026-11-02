import importlib
import logging
import os

from ableton.v2.control_surface import ControlSurface

from . import commands
from .server import Server, PORT

logger = logging.getLogger("livearranger")


class Manager(ControlSurface):
    def __init__(self, c_instance):
        ControlSurface.__init__(self, c_instance)
        self._start_logging()
        self.server = None
        try:
            self.server = Server(self._dispatch, logger)
            self.schedule_message(0, self._tick)
            self.show_message("LiveArranger: listening on 127.0.0.1:%d" % PORT)
            logger.info("LiveArranger started on port %d", PORT)
        except OSError as e:
            self.show_message("LiveArranger: couldn't bind port %d (%s)" % (PORT, e))
            logger.error("bind failed: %s", e)

    def _start_logging(self):
        log_dir = os.path.join(os.path.dirname(os.path.realpath(__file__)), "logs")
        os.makedirs(log_dir, exist_ok=True)
        self._log_handler = logging.FileHandler(os.path.join(log_dir, "livearranger.log"))
        self._log_handler.setFormatter(logging.Formatter("(%(asctime)s) [%(levelname)s] %(message)s"))
        logger.addHandler(self._log_handler)
        logger.setLevel(logging.INFO)

    def _tick(self):
        try:
            self.server.process()
        except Exception:
            logger.exception("tick failed")
        try:
            on_tick = getattr(commands, "on_tick", None)  # hot-reloadable per-tick hook
            if on_tick:
                on_tick(self)
        except Exception:
            logger.exception("on_tick failed")
        self.schedule_message(1, self._tick)  # ~100 ms

    def _dispatch(self, cmd, args):
        if cmd == "reload":
            importlib.reload(commands)
            logger.info("reloaded commands")
            return "reloaded"
        return commands.dispatch(self, cmd, args)

    def disconnect(self):
        logger.info("disconnecting")
        if self.server:
            self.server.shutdown()
        logger.removeHandler(self._log_handler)
        super().disconnect()
