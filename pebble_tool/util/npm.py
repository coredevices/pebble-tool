# encoding: utf-8


import os
import subprocess

from pebble_tool.exceptions import ToolError
from pebble_tool.util.versions import version_to_key


def check_npm():
    try:
        npm_version = subprocess.check_output(["npm", "--version"], text=True).strip()
        if version_to_key(npm_version)[0] < 3:
            raise ToolError("We require npm3; you are using version {}.".format(npm_version))
    except OSError:
        raise ToolError("You must have npm ≥ 3.0.0 available on your path.")
    except subprocess.CalledProcessError:
        raise ToolError("Your npm installation appears to be broken.")


def invoke_npm(args, cwd=None):
    check_npm()
    subprocess.check_call(["npm"] + args, cwd=cwd)


def sanity_check():
    if not os.path.exists('node_modules'):
        return
    for d in os.listdir('node_modules'):
        if not os.path.isdir(d):
            continue
        if 'node_modules' in os.listdir(os.path.join('node_modules', d)):
            raise ToolError("Conflicting npm dependency in {}: {}. Please resolve before continuing."
                            .format(d, os.listdir(os.path.join('node_modules', d, 'node_modules'))[0]))
