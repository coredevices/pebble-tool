
__author__ = 'katharine'

import hashlib
import platform
from contextlib import closing
import errno
import json
import logging
import os
from progressbar import ProgressBar, Percentage, Bar, FileTransferSpeed, Timer, UnknownLength
import requests
import shutil
import subprocess
import sys
import tempfile
import tarfile
import textwrap

from pebble_tool.exceptions import SDKInstallError, MissingSDK
from pebble_tool.sdk import eng_dash
from pebble_tool.sdk.requirements import Requirements
from pebble_tool.util import get_persist_dir
from pebble_tool.util.config import config
from pebble_tool.util.npm import invoke_npm
from pebble_tool.util.versions import version_to_key

logger = logging.getLogger("pebble_tool.sdk.manager")

_FALLBACK_PLATFORMS = ('aplite', 'basalt', 'chalk', 'diorite', 'emery', 'flint', 'gabbro')


def _uv_executable():
    """Locate the `uv` binary. Prefer one on PATH; fall back to a few well-known
    install locations. Raises SDKInstallError if uv isn't installed."""
    uv = shutil.which("uv")
    if uv:
        return uv
    for candidate in (
        os.path.expanduser("~/.local/bin/uv"),
        os.path.expanduser("~/.cargo/bin/uv"),
        "/opt/homebrew/bin/uv",
        "/usr/local/bin/uv",
    ):
        if os.path.exists(candidate):
            return candidate
    raise SDKInstallError(
        "uv not found. Install it from https://docs.astral.sh/uv/ — pebble-tool "
        "uses uv to manage SDK virtual environments so the Python version is "
        "always consistent with pebble-tool itself.")


def _create_sdk_venv(venv_path):
    """Create an SDK venv pinned to the same Python pebble-tool itself runs
    under. Using uv (not `python -m venv`) keeps the venv's interpreter version
    and pebble-tool's interpreter version in lockstep, so a brew/python upgrade
    can't leave a stale venv behind."""
    uv = _uv_executable()
    subprocess.check_call([uv, "venv", "--python", sys.executable, venv_path])


def _venv_pip_install(venv_path, *args, cwd=None):
    """Install packages into the SDK venv using uv pip."""
    uv = _uv_executable()
    venv_python = os.path.join(venv_path, "bin", "python")
    subprocess.check_call([uv, "pip", "install", "--python", venv_python, *args], cwd=cwd)

def get_pebble_platforms(sdk_path=None):
    """Get platforms from the installed SDK, with fallback to hardcoded list."""
    try:
        if sdk_path is None:
            from pebble_tool.sdk import sdk_manager
            sdk_path = sdk_manager.current_path
            if sdk_path is None:
                return _FALLBACK_PLATFORMS

        # Try standard SDK structure: <sdk-core>/pebble/common/tools/pebble_sdk_platform.py
        platform_file = os.path.join(sdk_path, 'pebble', 'common', 'tools', 'pebble_sdk_platform.py')
        # Also try tintin structure: <sdk>/common/tools/pebble_sdk_platform.py
        if not os.path.exists(platform_file):
            platform_file = os.path.join(sdk_path, 'common', 'tools', 'pebble_sdk_platform.py')

        if os.path.exists(platform_file):
            import importlib.util
            spec = importlib.util.spec_from_file_location("pebble_sdk_platform", platform_file)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return tuple(module.pebble_platforms.keys())
    except Exception:
        pass
    return _FALLBACK_PLATFORMS

class SDKManager(object):
    DOWNLOAD_SERVER = "https://sdk.repebble.com"
    DOWNLOAD_SERVER_DEFAULT = "https://sdk.repebble.com"

    def __init__(self, sdk_dir=None):
        self.sdk_dir = os.path.normpath(sdk_dir or os.path.join(get_persist_dir(), "SDKs"))
        if not os.path.exists(self.sdk_dir):
            os.makedirs(self.sdk_dir)

    @property
    def use_eng_dash(self):
        """Prefer eng-dash (channel-aware SDK registry) unless the user
        has explicitly pointed us at a legacy sdk.repebble.com-style
        server via --server or PEBBLE_SDK_SERVER. The explicit override
        wins so a developer can still work against a staging mirror."""
        if os.environ.get("PEBBLE_SDK_SERVER"):
            return False
        return self.DOWNLOAD_SERVER == self.DOWNLOAD_SERVER_DEFAULT

    def list_local_sdks(self):
        sdks = []
        try:
            for dir in os.listdir(self.sdk_dir):
                dir = os.path.join(self.sdk_dir, dir)
                if os.path.islink(dir):
                    continue
                manifest_path = os.path.join(dir, 'sdk-core', 'manifest.json')
                if not os.path.exists(manifest_path):
                    continue
                with open(manifest_path) as f:
                    try:
                        sdks.append(json.load(f))
                    except ValueError:
                        pass
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise
            return []

        return sdks

    def list_local_sdk_versions(self):
        return {x['version'] for x in self.list_local_sdks()}

    def list_remote_sdks(self):
        if self.use_eng_dash:
            token = eng_dash.firebase_token_if_logged_in()
            try:
                payload = eng_dash.list_releases(token=token)
            except SDKInstallError as e:
                logger.warning("eng-dash list failed (%s); falling back to %s", e, self.DOWNLOAD_SERVER)
                payload = None
            if payload is not None:
                # Public (production) releases get no suffix in `pebble sdk list`.
                return [
                    {
                        'version': r['version'],
                        'channel': '' if r.get('track') == 'production' else r.get('track') or '',
                        'url': None,
                    }
                    for r in payload.get('releases') or []
                ]
            # No releases visible → fall through to legacy so a stale SDK is
            # still listable while eng-dash is empty.
        sdks = self.request("/v1/files/sdk-core?channel={}".format(self.get_channel())).json()
        return sdks['files']

    def get_remote_channel_label(self):
        """Human-friendly channel/track label from eng-dash: used by `pebble
        sdk list` so the user can see which track decides their SDK. None
        when eng-dash isn't reachable or returned nothing — the caller
        prints the legacy label (`sdk-channel` config) in that case."""
        if not self.use_eng_dash:
            return None
        token = eng_dash.firebase_token_if_logged_in()
        try:
            payload = eng_dash.list_releases(token=token)
        except SDKInstallError:
            return None
        if payload is None:
            return None
        channel = payload.get('channel')
        track = payload.get('track')
        if channel and track and channel != track:
            return "{} / track {}".format(channel, track)
        return track or channel

    def uninstall_sdk(self, version):
        current_sdk = self.get_current_sdk()
        shutil.rmtree(self.root_path_for_sdk(version))
        if current_sdk == version:
            current_sdks = sorted(self.list_local_sdk_versions(), reverse=True, key=version_to_key)
            if len(current_sdks) > 0:
                self.set_current_sdk(current_sdks[0])
            else:
                os.unlink(self._current_path)

    def install_from_url(self, url, sha256=None, toolchain_override=None):
        with self._download_to_tempfile(url, sha256=sha256, label="Downloading..."):
            pass  # fall through — download already in self._last_tempfile
        f = self._last_tempfile
        try:
            self._install_from_handle(f, toolchain_override=toolchain_override)
        finally:
            f.close()
            self._last_tempfile = None

    def install_toolchain_from_url(self, url, sdk_version, platform_name, sha256=None):
        with self._download_to_tempfile(url, sha256=sha256, label="Downloading toolchain..."):
            pass
        f = self._last_tempfile
        try:
            self._install_toolchain_from_handle(f, sdk_version, platform_name)
        finally:
            f.close()
            self._last_tempfile = None

    def _download_to_tempfile(self, url, sha256=None, label="Downloading..."):
        """Stream `url` to a tempfile with a progress bar and (when
        provided) verify its sha256 before returning. The file is left in
        self._last_tempfile for the caller to consume; the context manager
        shape is only there so we can keep the try/finally shape callers
        were relying on previously."""
        print(label)
        # No HEAD probe: R2 presigned URLs are signed for GET only, so a
        # HEAD gets 403. Content-Length from the GET drives the bar.
        response = requests.get(url, stream=True)
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))

        bar = ProgressBar(
            # Chunked responses carry no Content-Length; a 0 max makes the
            # first update() raise.
            maxval=total_size or UnknownLength,
            widgets=[
                Percentage(),
                Bar(marker='=', left='[', right=']'),
                ' ',
                FileTransferSpeed(),
                ' ',
                Timer(format='%s'),
            ],
        )
        bar.start()

        f = tempfile.TemporaryFile()
        hasher = hashlib.sha256() if sha256 else None
        try:
            for content in response.iter_content(1024 * 32):
                bar.update(bar.currval + len(content))
                f.write(content)
                if hasher is not None:
                    hasher.update(content)
            bar.finish()
            if hasher is not None:
                got = hasher.hexdigest()
                if got.lower() != sha256.lower():
                    raise SDKInstallError(
                        "sha256 mismatch for {}: expected {}, got {}".format(url, sha256, got),
                    )
            f.flush()
            f.seek(0)
            self._last_tempfile = f
        except BaseException:
            f.close()
            raise

        class _Noop(object):
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *_a):
                return False
        return _Noop()

    def install_from_path(self, path):
        with open(path, 'rb') as f:
            self._install_from_handle(f, toolchain_dir=os.path.dirname(os.path.abspath(path)))

    def _install_from_handle(self, f, toolchain_dir=None, toolchain_override=None):
        """Extract an sdk-core tarball into self.sdk_dir and install the
        matching toolchain.
        toolchain_override: (url, platform_label, sha256|None) tuple from
        eng-dash. When set, replaces the DOWNLOAD_SERVER-probe path — the
        caller has already picked which toolchain fits this host, so we
        don't double-fetch metadata from the legacy server. toolchain_dir
        still wins over an override so a `pebble sdk install path/to.tar`
        with a sidecar toolchain keeps working."""
        path = None
        try:
            print("Extracting...")
            with tarfile.open(fileobj=f, mode="r:*") as t:
                with closing(t.extractfile('sdk-core/manifest.json')) as f_manifest:
                    sdk_info = json.load(f_manifest)
                path = os.path.normpath(os.path.join(self.sdk_dir, sdk_info['version']))
                if os.path.exists(path):
                    raise SDKInstallError("SDK {} is already installed.".format(sdk_info['version']))
                contents = t.getnames()
                for filename in contents:
                    if filename.startswith('/') or '..' in filename:
                        raise SDKInstallError("SDK contained a questionable file: {}".format(filename))
                if not path.startswith(self.sdk_dir):
                    raise SDKInstallError("Suspicious version number: {}".format(sdk_info['version']))
                Requirements(sdk_info['requirements']).ensure_satisfied()
                os.mkdir(os.path.join(self.sdk_dir, sdk_info['version']))
                t.extractall(path)
            venv_path = os.path.join(path, ".venv")
            print("Preparing venv (uv)...")
            _create_sdk_venv(venv_path)
            print("Installing dependencies (uv pip)...")
            _venv_pip_install(venv_path, "-r", os.path.join(path, "sdk-core", "requirements.txt"))
            package_json = os.path.join(path, "sdk-core", "package.json")
            if os.path.exists(package_json):
                print("Installing JS dependencies... (this may take a while)")
                node_modules_folder = os.path.join(path, "node_modules")
                os.mkdir(node_modules_folder)
                shutil.copy2(package_json, os.path.join(path, "package.json"))
                invoke_npm(["install", "--silent"], cwd=path)

            self.set_current_sdk(sdk_info['version'])

            platform_name = "mac" if platform.system() == "Darwin" else "linux"
            arch = platform.machine()

            # A toolchain shipped alongside a local SDK file wins over the download server.
            local_toolchain = self._local_toolchain(toolchain_dir, platform_name, arch)
            if local_toolchain is not None:
                toolchain_path, toolchain_platform = local_toolchain
                with open(toolchain_path, 'rb') as tf:
                    self._install_toolchain_from_handle(tf, sdk_info['version'], toolchain_platform)
            elif toolchain_override is not None:
                tc_url, tc_platform, tc_sha256 = toolchain_override
                self.install_toolchain_from_url(
                    tc_url, sdk_info['version'], tc_platform, sha256=tc_sha256,
                )
            else:
                # Try architecture-specific toolchain first, fall back to platform-only
                arch_specific_url = f"{self.DOWNLOAD_SERVER}/releases/{sdk_info['version']}/toolchain-{platform_name}-{arch}.tar.gz"
                fallback_url = f"{self.DOWNLOAD_SERVER}/releases/{sdk_info['version']}/toolchain-{platform_name}.tar.gz"

                try:
                    response = requests.head(arch_specific_url)
                    if response.status_code == 200:
                        self.install_toolchain_from_url(arch_specific_url, sdk_info['version'], f"{platform_name}-{arch}")
                    else:
                        self.install_toolchain_from_url(fallback_url, sdk_info['version'], platform_name)
                except requests.RequestException:
                    self.install_toolchain_from_url(fallback_url, sdk_info['version'], platform_name)

            print("Done.")
        except BaseException:
            print("Failed.")
            try:
                if path is not None and os.path.exists(path):
                    print("Cleaning up failed install...")
                    shutil.rmtree(path)
                    print("Done.")
            except OSError:
                print("Cleanup failed.")
            raise

    @staticmethod
    def _local_toolchain(toolchain_dir, platform_name, arch):
        if toolchain_dir is None:
            return None
        for toolchain_platform in (f"{platform_name}-{arch}", platform_name):
            candidate = os.path.join(toolchain_dir, f"toolchain-{toolchain_platform}.tar.gz")
            if os.path.exists(candidate):
                return candidate, toolchain_platform
        return None

    def _install_toolchain_from_handle(self, f, sdk_version, platform_name):
        print("Extracting toolchain...")

        toolchain_path = os.path.normpath(os.path.join(self.sdk_dir, sdk_version, "toolchain"))

        with tarfile.open(fileobj=f, mode="r:*") as t:
            contents = t.getnames()

            for filename in contents:
                if filename.startswith('/') or '..' in filename:
                    raise SDKInstallError("SDK contained a questionable file: {}".format(filename))
            if not toolchain_path.startswith(self.sdk_dir):
                raise SDKInstallError("Suspicious version number: {}".format(toolchain_path))

            os.mkdir(os.path.join(self.sdk_dir, sdk_version, "toolchain"))
            t.extractall(toolchain_path)

        # Tarball root is toolchain-<platform>[-<arch>]; which one depends on
        # what the server had (an arm64 slot may hold the platform-only
        # file), so flatten whatever single toolchain-* dir it shipped.
        roots = [d for d in os.listdir(toolchain_path) if d.startswith('toolchain-')]
        if len(roots) != 1:
            raise SDKInstallError("Unexpected toolchain layout (expected toolchain-{}): {}".format(platform_name, roots))
        root = os.path.join(toolchain_path, roots[0])
        for folder in os.listdir(root):
            shutil.move(os.path.join(root, folder), toolchain_path)
        os.rmdir(root)

    def install_remote_sdk(self, version):
        if self.use_eng_dash:
            try:
                return self._install_remote_sdk_eng_dash(version)
            except SDKInstallError as e:
                logger.warning(
                    "eng-dash SDK install failed (%s); falling back to %s",
                    e, self.DOWNLOAD_SERVER,
                )
        return self._install_remote_sdk_legacy(version)

    def _install_remote_sdk_legacy(self, version):
        sdk_info = self.request("/v1/files/sdk-core/{}?channel={}".format(version, self.get_channel())).json()
        if 'version' not in sdk_info:
            raise SDKInstallError("SDK {} could not be downloaded.".format(version))
        path = os.path.normpath(os.path.join(self.sdk_dir, sdk_info['version']))
        manifest_path = os.path.join(path, 'sdk-core', 'manifest.json')

        # Check if SDK is already installed by validating the manifest
        if os.path.exists(path):
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path) as f:
                        json.load(f)
                    raise SDKInstallError("SDK {} is already installed.".format(sdk_info['version']))
                except (ValueError, IOError):
                    # Manifest is invalid or unreadable, clean up broken install
                    print("Detected broken installation, cleaning up...")
                    shutil.rmtree(path)
                    print("Cleaned up broken installation.")
            else:
                # Directory exists but no manifest, clean up broken install
                print("Detected broken installation, cleaning up...")
                shutil.rmtree(path)
                print("Cleaned up broken installation.")

        # For now, we ignore this field aside from bailing if it has content.
        Requirements(sdk_info['requirements']).ensure_satisfied()
        self._license_prompt()
        self.install_from_url(sdk_info['url'])

    def _install_remote_sdk_eng_dash(self, version):
        token = eng_dash.firebase_token_if_logged_in()
        if version in ("latest", "", None):
            payload = eng_dash.latest(token=token)
        else:
            payload = eng_dash.release(version, token=token)
        if payload is None:
            raise SDKInstallError(
                "eng-dash has no SDK release '{}' available on your channel. Run `pebble login` "
                "first, or ask an admin to publish a release on dash.repebble.com/releases/sdk."
                .format(version),
            )
        resolved_version = payload.get('version')
        if not resolved_version:
            raise SDKInstallError("eng-dash returned a release without a version")
        sdk_core = payload.get('sdk_core') or {}
        if not sdk_core.get('url'):
            raise SDKInstallError("eng-dash release {} has no sdk-core artifact".format(resolved_version))
        toolchain, toolchain_platform = eng_dash.pick_toolchain(payload.get('toolchains') or {})
        if toolchain is None:
            logger.warning(
                "eng-dash release %s has no toolchain for this host; falling back to %s for toolchain only.",
                resolved_version, self.DOWNLOAD_SERVER,
            )
        else:
            logger.debug("eng-dash: toolchain kind=%s", toolchain_platform)

        print("Installing SDK {} (channel {} / track {})...".format(
            resolved_version,
            payload.get('channel') or 'default',
            payload.get('track') or 'production',
        ))
        self._license_prompt()

        path = os.path.normpath(os.path.join(self.sdk_dir, resolved_version))
        manifest_path = os.path.join(path, 'sdk-core', 'manifest.json')
        if os.path.exists(path):
            if os.path.exists(manifest_path):
                try:
                    with open(manifest_path) as f:
                        json.load(f)
                    raise SDKInstallError("SDK {} is already installed.".format(resolved_version))
                except (ValueError, IOError):
                    print("Detected broken installation, cleaning up...")
                    shutil.rmtree(path)
            else:
                print("Detected broken installation, cleaning up...")
                shutil.rmtree(path)

        toolchain_override = None
        if toolchain is not None:
            toolchain_override = (
                toolchain['url'],
                toolchain_platform,
                toolchain.get('sha256'),
            )

        self.install_from_url(
            sdk_core['url'],
            sha256=sdk_core.get('sha256'),
            toolchain_override=toolchain_override,
        )

    def _license_prompt(self):
        prompt = textwrap.dedent("""
        By using the Pebble SDK, you agree to the following:

        PEBBLE TERMS OF USE
        https://developer.repebble.com/legal/terms-of-use/index.html
        
        PEBBLE DEVELOPER LICENSE
        https://developer.repebble.com/legal/sdk-license/index.html
        """)
        print(prompt)

    def set_current_sdk(self, version):
        path = os.path.join(self.sdk_dir, version)
        if not os.path.exists(path):
            raise SDKInstallError("SDK version {} is not currently installed.".format(version))
        # PBL-24516: This isn't going to play nice on Windows.
        try:
            os.unlink(self._current_path)
        except (OSError, TypeError):
            pass
        os.symlink(path, self._current_path)

    def get_current_sdk(self):
        if self.current_path is None:
            return None
        manifest_path = os.path.join(self.current_path, "manifest.json")
        if not os.path.exists(manifest_path):
            return None
        with open(manifest_path) as f:
            return json.load(f)['version']

    @classmethod
    def set_channel(cls, channel):
        config.set('sdk-channel', channel)

    @classmethod
    def get_channel(cls):
        return config.get('sdk-channel', '')

    def make_tintin_sdk(self, path):
        path = os.path.realpath(os.path.expanduser(path))
        dest_path = os.path.join(self.sdk_dir, 'tintin')
        if not os.path.exists(os.path.join(path, 'wscript')):
            raise SDKInstallError("No tintin found at {}".format(path))
        if os.path.exists(dest_path):
            raise SDKInstallError("tintin SDK already set up. uninstall before making changes.")
        build_path = os.path.join(path, 'build')
        sdk_path = os.path.join(build_path, 'sdk')
        os.mkdir(dest_path)
        env_path = os.path.join(dest_path, '.venv')
        if os.path.exists(os.path.join(sdk_path, 'package.json')):
            shutil.copy2(os.path.join(sdk_path, 'package.json'), os.path.join(dest_path, 'package.json'))
            node_modules_path = os.path.join(dest_path, 'node_modules')
            os.mkdir(node_modules_path)
        dest_path = os.path.join(dest_path, 'sdk-core')
        os.mkdir(os.path.join(dest_path))
        pebble_path = os.path.join(dest_path, 'pebble')
        os.mkdir(pebble_path)

        # A symlink doesn't work for some reason; instead write a python script that invokes waf using whatever
        # interpreter we used to invoke it.
        with open(os.path.join(pebble_path, 'waf'), 'w') as f:
            f.write("""#!/usr/bin/env python
import subprocess
import sys
subprocess.call([sys.executable, {}] + sys.argv[1:])
""".format(repr(os.path.join(sdk_path, 'waf'))))
            os.chmod(os.path.join(pebble_path, 'waf'), 0o755)
        for platform in get_pebble_platforms(sdk_path):
            os.mkdir(os.path.join(pebble_path, platform))
            os.symlink(os.path.join(sdk_path, platform, 'include'), os.path.join(pebble_path, platform, 'include'))
            os.symlink(os.path.join(sdk_path, platform, 'lib'), os.path.join(pebble_path, platform, 'lib'))
            os.mkdir(os.path.join(pebble_path, platform, 'qemu'))
            os.symlink(os.path.join(build_path, 'qemu_micro_flash.bin'),
                       os.path.join(pebble_path, platform, 'qemu', 'qemu_micro_flash.bin'))
            os.symlink(os.path.join(build_path, 'qemu_spi_flash.bin'),
                       os.path.join(pebble_path, platform, 'qemu', 'qemu_spi_flash.bin'))

        os.symlink(os.path.join(sdk_path, 'common/'), 
                   os.path.join(pebble_path, 'common'))

        with open(os.path.join(dest_path, 'manifest.json'), 'w') as f:
            json.dump({
                'requirements': [],
                'version': 'tintin',
                'type': 'sdk-core',
                'channel': '',
            }, f)

        print("Preparing venv (uv)...")
        _create_sdk_venv(env_path)
        print("Installing dependencies (uv pip)...")
        print("This may fail installing Pillow==2.0.0. In that case, question why we still force 2.0.0 anyway.")
        _venv_pip_install(env_path, "-r", os.path.join(path, "requirements.txt"), cwd=path)
        # if sys.platform.startswith('darwin'):
        #     platform = 'osx'
        #     subprocess.check_call([os.path.join(env_path, "bin", "python"), "-m", "pip", "install", "-r",
        #                            os.path.join(path, "requirements-{}.txt".format(platform))])
        # elif sys.platform.startswith('linux'):
        #     print("Linux requirements-linux.txt not implemented, skipping installation.")
        # else:
        #     raise SDKInstallError("Couldn't figure out what requirements to install.")
        if os.path.exists(os.path.join(dest_path, '..', 'node_modules')):
            print("Installing JS dependencies... (this may take a while)")
            invoke_npm(["install", "--silent"], cwd=os.path.join(dest_path, '..'))

        self.set_current_sdk('tintin')
        print("Generated an SDK linked to {}.".format(path))

    @property
    def current_path(self):
        path = self._current_path
        if not os.path.exists(path):
            return None
        return os.path.join(path, 'sdk-core')

    @property
    def _current_path(self):
        return os.path.join(self.sdk_dir, "current")

    def request(self, path, *args):
        return requests.get("{}{}".format(self.DOWNLOAD_SERVER, path), *args)

    def root_path_for_sdk(self, version):
        path = os.path.join(self.sdk_dir, version)
        if not os.path.exists(path):
            raise MissingSDK("SDK {} is not installed.".format(version))
        return path

    def path_for_sdk(self, version):
        path = os.path.join(self.root_path_for_sdk(version), 'sdk-core')
        if not os.path.exists(path):
            raise MissingSDK("SDK {} is not installed.".format(version))
        return path

    @staticmethod
    def parse_version(version_string):
        return tuple(list(map(int, version_string.split('-', 1)[0].split('.', 2))) + [0, 0])[:3]
