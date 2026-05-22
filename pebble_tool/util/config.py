
__author__ = 'katharine'

import json
import os.path
import threading

from . import get_persist_dir


class Config(object):
    def __init__(self):
        self.path = os.path.join(get_persist_dir(), 'settings.json')
        self.lock = threading.Lock()
        self.skip_save = False
        try:
            with open(self.path) as f:
                self.content = json.load(f)
        except IOError:
            self.content = {}
        except json.JSONDecodeError:
            self.content = {}

    def save(self):
        # Set by `wipe --everything` so cleanup doesn't recreate the directory
        # we just deleted.
        if self.skip_save:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump(self.content, f, indent=4)

    def get(self, key, default=None):
        return self.content.get(key, default)

    def set(self, key, value):
        self.content[key] = value

    def setdefault(self, key, default=None):
        return self.content.setdefault(key, default)

config = Config()
