

from packaging.requirements import Requirement
import os
import re
import subprocess
import sys

from pebble_tool.exceptions import ToolError
from pebble_tool.version import __version__

__author__ = 'katharine'
__all__ = ['Requirements']


class Requirements(object):
    def __init__(self, requirements):
        self.requirements = [Requirement(x) for x in requirements]
        self._version_cache = {}

    def unsatisfied_requirements(self):
        unsatisfied = []
        for req in self.requirements:
            fn_name = 'has_' + re.sub(r'[^\w]', '_', req.name)
            fn = getattr(self, fn_name, None)
            if not callable(fn) or not fn(req):
                unsatisfied.append(req)
        return unsatisfied

    def ensure_satisfied(self):
        unsatisfied = self.unsatisfied_requirements()
        if len(unsatisfied) > 0:
            raise ToolError("This SDK has the following unmet requirements: {}\n"
                            "Try updating the pebble tool.".format(", ".join(str(x) for x in unsatisfied)))

    def has_pebble_tool(self, req):
        return self._pebble_tool_version in req.specifier

    def has_pypkjs(self, req):
        return self._pypkjs_version is not None and self._pypkjs_version in req.specifier

    def has_qemu(self, req):
        version = self._qemu_version
        if version is None:
            return False
        return version.replace('-pebble', '.') in req.specifier

    @property
    def _pebble_tool_version(self):
        return __version__

    @property
    def _qemu_version(self):
        if 'qemu' not in self._version_cache:
            qemu_path = os.environ.get('PEBBLE_QEMU_PATH', 'qemu-pebble')
            try:
                result = subprocess.check_output([qemu_path, '--version'], stderr=subprocess.STDOUT)
            except (subprocess.CalledProcessError, OSError):
                version = None
            else:
                try:
                    version = re.search(r'version ([^\s,]+)', result).group(1)
                except AttributeError:
                    version = None
            self._version_cache['qemu'] = version
        return self._version_cache['qemu']

    @property
    def _pypkjs_version(self):
        if 'pypkjs' not in self._version_cache:
            pypkjs_path = os.environ.get('PHONESIM_PATH', 'phonesim.py')
            try:
                result = subprocess.check_output([sys.executable, pypkjs_path, '--version'], stderr=subprocess.STDOUT)
            except subprocess.CalledProcessError:
                version = '1.0.4'  # The version before we started including version numbers.
            except OSError:
                version = None
            else:
                try:
                    version = re.search(r'v([^\s]+)', result).group(1)
                except AttributeError:
                    version = None
            self._version_cache['pypkjs'] = version
        return self._version_cache['pypkjs']
