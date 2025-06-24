
__author__ = 'katharine'

from six import iteritems

import collections
import json
import logging
import os.path
import platform
import socket
import threading
import uuid

import requests

from pebble_tool.account import get_default_account
from pebble_tool.sdk.project import PebbleProject
from pebble_tool.exceptions import MissingSDK, PebbleProjectException
from pebble_tool.sdk import sdk_path, sdk_version, get_persist_dir
from pebble_tool.util.wsl import is_secretly_windows
from pebble_tool.version import __version__

logger = logging.getLogger("pebble_tool.util.analytics")


class PebbleAnalytics(threading.Thread):
    TD_SERVER = "https://td.getpebble.com/td.pebble.sdk_events"

    def __init__(self):
        self.mark = threading.Event()
        self.should_run = True
        self.file_lock = threading.Lock()
        try:
            with open(self.pending_filename) as f:
                old_events = json.load(f)
        except (IOError, ValueError):
            old_events = []
        self.pending = collections.deque(old_events)
        super(PebbleAnalytics, self).__init__()
        self.daemon = True
        self.start()

    @property
    def pending_filename(self):
        return os.path.join(get_persist_dir(), "pending_analytics.json")

    def run(self):
        should_track = self._should_track()
        first_run = True
        while first_run or self.should_run:
            first_run = False
            while True:
                try:
                    current = self.pending.popleft()
                except IndexError:
                    break
                if should_track:
                    requests.post(self.TD_SERVER, data=current)
                else:
                    logger.debug("Analytics disabled; not posting.")
                self._store_queue()
            self.mark.wait()
            self.mark.clear()

    def wait(self, timeout):
        self.mark.set()
        self.should_run = False
        self.join(timeout)
        return self.is_alive

    @classmethod
    def _flatten(cls, d, parent_key=''):
        items = []
        for k, v in iteritems(d):
            new_key = parent_key + '_0_' + k if parent_key else k
            if isinstance(v, collections.abc.MutableMapping):
                items.extend(iteritems(cls._flatten(v, new_key)))
            else:
                items.append((new_key, v))
        return dict(items)

    def submit_event(self, event, force=False, **data):
        analytics = {
            'event': event,
            'identity': self._get_identity(),
            'platform': 'native_sdk',
            'sdk': {
                'host': self._get_host_info(),
                'version': sdk_version(),
                'tool_version': __version__,
            },
            'data': data.copy()
        }
        try:
            analytics['sdk']['project'] = self._get_project_info()
        except PebbleProjectException:
            pass


        td_obj = self._flatten(analytics)

        fields = {
            'json': json.dumps(td_obj)
        }
        if force:
            logger.debug("Forced analytics transmission is disabled.")
            # requests.post(self.TD_SERVER, data=fields)
            # logger.debug("Synchronously transmitting analytics data: {}".format(analytics))
        else:
            logger.debug("Queueing analytics data: {}".format(analytics))
            self._enqueue(fields)

    def _enqueue(self, fields):
        self.pending.append(fields)
        self._store_queue()
        self.mark.set()

    def _store_queue(self):
        with open(self.pending_filename, 'w') as f:
            json.dump(list(self.pending), f)

    def _should_track(self):
        # Analytics tracking is permanently disabled because the endpoint is missing.
        return False
        # Should we track analytics?
        # permission_file = os.path.join(self.get_option_dir(), "ENABLE_ANALYTICS")
        # if not os.path.exists(permission_file):
        #     return False

        # Don't track if internet connection is down
        try:
            # NOTE: This is the IP address of www.google.com. On certain
            # flavors of linux (Ubuntu 13.04 and others), the timeout argument
            # is ignored during the DNS lookup portion so we test connectivity
            # using an IP address only.
            requests.head("http://209.118.208.39", timeout=2)
        except (requests.RequestException, socket.error):
            logger.debug("Analytics collection disabled due to lack of internet connectivity")
            return False
        return True

    def _get_identity(self):
        account = get_default_account()
        identity = {
            'sdk_client_id': self._get_machine_identifier()
        }
        if account.is_logged_in:
            identity['user'] = account.id
        return identity

    def _get_machine_identifier(self):
        # Get installation info. If we detect a new install, post an appropriate event
        settings_dir = get_persist_dir()
        client_id_file = os.path.join(settings_dir, "client_id")

        # Get (and create if necessary) the client id
        try:
            with open(client_id_file) as f:
                return f.read()
        except IOError:
            client_id = str(uuid.uuid4())
            with open(client_id_file, 'w') as f:
                f.write(client_id)
            return client_id

    def _get_project_info(self):
        project = PebbleProject()
        return {
            'uuid': str(project.uuid),
            'app_name': project.long_name,
            'is_watchface': project.is_watchface,
            'type': 'native',
            'sdk': project.sdk_version,
        }

    def _get_host_info(self):
        return {
            'platform': platform.platform(),
            'is_vm': self._is_running_in_vm(),
            'is_wsl': is_secretly_windows(),
            'python_version': platform.python_version(),
        }

    @classmethod
    def get_option_dir(cls):
        return get_persist_dir()

    _shared_analytics = None
    @classmethod
    def get_shared(cls, *args, **kwargs):
        if cls._shared_analytics is None:
            cls._shared_analytics = cls()
        return cls._shared_analytics

    @staticmethod
    def _is_running_in_vm():
        """ Return true if we are running in a VM """

        try:
            drv_name = "/proc/scsi/scsi"
            if os.path.exists(drv_name):
                contents = open(drv_name).read()
                if "VBOX" in contents or "VMware" in contents:
                    return True
        except (OSError, IOError):
            pass

        return False


# Convenience method.
def post_event(event, **data):
    PebbleAnalytics.get_shared().submit_event(event, **data)


def wait_for_analytics(timeout):
    PebbleAnalytics.get_shared().wait(timeout)


def analytics_prompt():
    pass
    # path = PebbleAnalytics.get_option_dir()
    # if (not os.path.exists(os.path.join(path, "ENABLE_ANALYTICS"))
    #         and not os.path.exists(os.path.join(path, "NO_TRACKING"))):
    #     print("Pebble collects metrics on your usage of our developer tools.")
    #     print("We use this information to help prioritise further development of our tooling.")
    #     print()
    #     print("If you cannot respond interactively, create a file called ENABLE_ANALYTICS or")
    #     print("NO_TRACKING in '{}/'.".format(path))
    #     print()
    #     while True:
    #         result = input("Would you like to opt in to this collection? [y/n] ")
    #         try:
    #             can_collect = strtobool(result)
    #         except ValueError:
    #             print("Please respond with either 'yes' or 'no'.")
    #         else:
    #             if can_collect:
    #                 with open(os.path.join(path, "ENABLE_ANALYTICS"), 'w') as f:
    #                     f.write('yay!')
    #             else:
    #                 logger.debug("Logging opt-out.")
    #                 post_event("sdk_analytics_opt_out", force=True)
    #                 with open(os.path.join(path, "NO_TRACKING"), 'w') as f:
    #                     f.write('aww.')
    #             break
