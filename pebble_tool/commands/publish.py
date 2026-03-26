__author__ = 'katharine'

import argparse
import contextlib
import json
import mimetypes
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

import requests
from colorama import Fore, Style, init as colorama_init

from pebble_tool.account import get_account
from pebble_tool.commands.base import BaseCommand
from pebble_tool.commands.screenshot import ScreenshotCommand
from pebble_tool.commands.sdk.project.build import BuildCommand
from pebble_tool.exceptions import BuildError, ToolError
from pebble_tool.sdk.project import PebbleProject

DEFAULT_APPSTORE_API_BASE = "https://appstore-api.repebble.com"


class PublishCommand(BaseCommand):
    """Builds and uploads a release to the appstore dashboard API using Firebase auth."""

    command = "publish"

    def _out(self, message, colour=Fore.CYAN, bright=False):
        style = Style.BRIGHT if bright else ""
        print("{}{}{}{}".format(style, colour, message, Style.RESET_ALL))

    def _step(self, message):
        self._out(message, colour=Fore.BLUE, bright=True)

    def _warn(self, message):
        self._out(message, colour=Fore.YELLOW, bright=True)

    def _ok(self, message):
        self._out(message, colour=Fore.GREEN, bright=True)

    @contextlib.contextmanager
    def _capture_console_output(self):
        # Capture fd-level output so subprocess output stays hidden unless the build fails.
        tmp = tempfile.TemporaryFile(mode="w+b")
        sys.stdout.flush()
        sys.stderr.flush()
        saved_out = os.dup(1)
        saved_err = os.dup(2)
        try:
            os.dup2(tmp.fileno(), 1)
            os.dup2(tmp.fileno(), 2)
            yield tmp
        finally:
            sys.stdout.flush()
            sys.stderr.flush()
            os.dup2(saved_out, 1)
            os.dup2(saved_err, 2)
            os.close(saved_out)
            os.close(saved_err)

    @classmethod
    def _post_with_wait_bar(cls, url, headers, data, files, timeout, label):
        response_holder = {}
        error_holder = {}
        done = threading.Event()

        def _worker():
            try:
                response_holder["response"] = requests.post(
                    url,
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=timeout,
                )
            except requests.RequestException as e:
                error_holder["error"] = e
            finally:
                done.set()

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

        width = 24
        tick = 0
        start = time.time()
        while not done.wait(0.25):
            pos = tick % width
            bar = "".join("=" if i < pos else " " for i in range(width))
            elapsed = int(time.time() - start)
            sys.stdout.write("\r{} [{}] {:>3}s".format(label, bar, elapsed))
            sys.stdout.flush()
            tick += 1

        elapsed = int(time.time() - start)
        if tick:
            sys.stdout.write("\r{} [{}] {:>3}s\n".format(label, "=" * width, elapsed))
            sys.stdout.flush()

        if "error" in error_holder:
            raise ToolError("Request failed: {}".format(error_holder["error"]))
        return response_holder["response"]

    def __call__(self, args):
        super(PublishCommand, self).__call__(args)
        colorama_init()

        firebase_id_token = (getattr(args, "firebase_id_token", None) or os.getenv("PEBBLE_FIREBASE_ID_TOKEN", "")).strip()
        if not firebase_id_token:
            account = get_account(auth_provider="firebase")
            if not account.is_logged_in:
                raise ToolError(
                    "Not logged in with Firebase. Run 'pebble login' first, "
                    "or pass --firebase-id-token / set PEBBLE_FIREBASE_ID_TOKEN for CI."
                )
            firebase_id_token = account.get_access_token()

        self._step("Appstore auth preflight...")
        print("API base: {}".format(args.api_base))
        me_payload = self._get_me_context(args.api_base, firebase_id_token)
        if me_payload is None or not self._has_linked_developer(me_payload):
            self._warn("No linked developer account found. Creating one now...")
            self._create_developer(args.api_base, firebase_id_token)
            me_payload = self._get_me_context(args.api_base, firebase_id_token)
            if me_payload is None or not self._has_linked_developer(me_payload):
                raise ToolError(
                    "Developer account is not linked on {}. "
                    "Run pebble login again for this environment or verify backend data.".format(args.api_base)
                )
        self._ok("Developer link check successful.")

        self._build_project(args)
        project = PebbleProject()
        pbw_path = self._pbw_path_for_project(project)
        if not os.path.exists(pbw_path):
            raise ToolError("Build completed but PBW was not found at {}".format(pbw_path))

        pbw_metadata = self._extract_pbw_metadata(pbw_path, project)
        upload_pbw_path, normalized_uuid, temp_pbw_path = self._create_uuid_normalized_pbw(pbw_path)
        if normalized_uuid and str(pbw_metadata.get("app_uuid")) != normalized_uuid:
            self._warn("Normalizing PBW UUID casing for upload: {} -> {}".format(pbw_metadata.get("app_uuid"), normalized_uuid))
            pbw_metadata["app_uuid"] = normalized_uuid
        desired_version = (
            (getattr(args, "version", None) or "").strip()
            or str(getattr(project, "version", "") or "").strip()
            or str(pbw_metadata.get("version") or "").strip()
        )
        self._step("PBW Metadata")
        print("Using PBW: {}".format(pbw_path))
        print("PBW app UUID: {}".format(pbw_metadata["app_uuid"]))
        print("Name: {}".format(pbw_metadata["app_name"]))
        print("PBW Version: {}".format(pbw_metadata["version"]))
        print("Publish Version: {}".format(desired_version))
        print("Platforms: {}".format(", ".join(pbw_metadata["platforms"])))

        app_lookup = ((me_payload.get("app_lookup") or {}).get("by_app_uuid") or {})
        app_id = self._lookup_app_id_case_insensitive(app_lookup, pbw_metadata["app_uuid"])

        gif_paths = []
        screenshot_paths = []
        resolved_app_id = None
        try:
            if app_id:
                self._ok("Resolved existing appstore app ID: {}".format(app_id))
                gif_paths, screenshot_paths = self._collect_screenshot_assets(args, pbw_metadata, allow_skip=True)
                self._step("Publishing release to Pebble Appstore...")
                response_payload = self._upload_release(
                    api_base=args.api_base,
                    app_id=app_id,
                    firebase_id_token=firebase_id_token,
                    pbw_path=upload_pbw_path,
                    version=desired_version,
                    release_notes=args.release_notes,
                    is_published=args.is_published,
                    gif_paths=gif_paths,
                    screenshot_paths=screenshot_paths,
                )
                resolved_app_id = app_id
            else:
                self._warn("No existing app mapping for UUID {}. Creating a new app...".format(pbw_metadata["app_uuid"]))
                create_details = self._collect_new_app_details(args, pbw_metadata, me_payload, default_version=desired_version)
                gif_paths, screenshot_paths = self._collect_screenshot_assets(args, pbw_metadata, allow_skip=False)
                self._step("Publishing new app to Pebble Appstore...")
                response_payload = self._create_app(
                    api_base=args.api_base,
                    firebase_id_token=firebase_id_token,
                    pbw_path=upload_pbw_path,
                    pbw_metadata=pbw_metadata,
                    create_details=create_details,
                    release_notes=args.release_notes,
                    is_published=args.is_published,
                    gif_paths=gif_paths,
                    screenshot_paths=screenshot_paths,
                )
                resolved_app_id = self._extract_app_id(response_payload)
                if not resolved_app_id:
                    me_after_create = self._get_me_context(args.api_base, firebase_id_token)
                    app_lookup_after = ((me_after_create or {}).get("app_lookup") or {}).get("by_app_uuid") or {}
                    resolved_app_id = self._lookup_app_id_case_insensitive(app_lookup_after, pbw_metadata["app_uuid"])

            self._print_upload_result(response_payload, resolved_app_id)
        finally:
            if temp_pbw_path and os.path.exists(temp_pbw_path):
                try:
                    os.unlink(temp_pbw_path)
                except OSError:
                    pass

    def _build_project(self, args):
        self._step("Building app...")
        build_args = argparse.Namespace(v=args.v, sdk=args.sdk, debug=False, args=[])
        with self._capture_console_output() as captured:
            try:
                BuildCommand()(build_args)
            except BuildError:
                captured.seek(0)
                build_log = captured.read().decode("utf-8", errors="replace").strip()
                if build_log:
                    raise ToolError("Build failed.\n{}".format(build_log))
                raise ToolError("Build failed.")
            except Exception as e:
                captured.seek(0)
                build_log = captured.read().decode("utf-8", errors="replace").strip()
                if build_log:
                    raise ToolError("Build failed: {}\n{}".format(e, build_log))
                raise ToolError("Build failed: {}".format(e))
        self._ok("App build successful.")

    @classmethod
    def _pbw_path_for_project(cls, project):
        return os.path.join(project.project_dir, "build", "{}.pbw".format(os.path.basename(project.project_dir)))

    @classmethod
    def _extract_pbw_metadata(cls, pbw_path, project):
        app_uuid = str(project.uuid) if getattr(project, "uuid", None) else "unknown"
        version = str(project.version)
        platforms = list(project.target_platforms)
        app_name = getattr(project, "long_name", None) or getattr(project, "short_name", None) or os.path.basename(project.project_dir)
        app_type = "watchface" if getattr(project, "is_watchface", False) else "watchapp"

        with zipfile.ZipFile(pbw_path) as zf:
            for metadata_name in ("appinfo.json", "manifest.json"):
                if metadata_name not in zf.namelist():
                    continue
                with zf.open(metadata_name) as f:
                    metadata = json.loads(f.read().decode("utf-8"))
                app_uuid = metadata.get("uuid", app_uuid)
                version = str(metadata.get("versionLabel", metadata.get("version", version)))
                platforms = metadata.get("targetPlatforms", platforms)
                app_name = metadata.get("longName", metadata.get("shortName", metadata.get("displayName", app_name)))
                watchapp = metadata.get("watchapp") or {}
                if watchapp.get("watchface") is True:
                    app_type = "watchface"
                elif watchapp:
                    app_type = "watchapp"
                break

        return {
            "app_uuid": app_uuid,
            "version": version,
            "platforms": platforms,
            "app_name": app_name,
            "app_type": app_type,
        }

    @classmethod
    def _create_uuid_normalized_pbw(cls, pbw_path):
        with zipfile.ZipFile(pbw_path, "r") as src:
            app_uuid = None
            for metadata_name in ("appinfo.json", "manifest.json"):
                if metadata_name not in src.namelist():
                    continue
                with src.open(metadata_name) as f:
                    metadata = json.loads(f.read().decode("utf-8"))
                if metadata.get("uuid"):
                    app_uuid = str(metadata["uuid"])
                    break

            if not app_uuid:
                return pbw_path, None, None
            lower_uuid = app_uuid.lower()
            if app_uuid == lower_uuid:
                return pbw_path, lower_uuid, None

            fd, tmp_path = tempfile.mkstemp(prefix="pebble-publish-", suffix=".pbw")
            os.close(fd)
            with zipfile.ZipFile(tmp_path, "w") as dst:
                for info in src.infolist():
                    data = src.read(info.filename)
                    if info.filename in ("appinfo.json", "manifest.json"):
                        try:
                            meta = json.loads(data.decode("utf-8"))
                            if meta.get("uuid"):
                                meta["uuid"] = str(meta["uuid"]).lower()
                                data = json.dumps(meta).encode("utf-8")
                        except Exception:
                            pass
                    dst.writestr(info, data)
            return tmp_path, lower_uuid, tmp_path

    @classmethod
    def _request_json(cls, method, url, token, timeout=60, json_body=None):
        try:
            response = requests.request(
                method,
                url,
                headers={"Authorization": "Bearer {}".format(token)},
                json=json_body,
                timeout=timeout,
            )
        except requests.RequestException as e:
            raise ToolError("{} {} failed: {}".format(method, url, e))

        payload = {}
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        return response, payload

    @classmethod
    def _get_me_context(cls, api_base, firebase_id_token):
        if not api_base:
            raise ToolError(
                "Missing appstore API base URL. Pass --api-base, set PEBBLE_APPSTORE_API_BASE, "
                "or use the default {}.".format(DEFAULT_APPSTORE_API_BASE)
            )
        me_url = "{}/api/v1/developer/me".format(api_base.rstrip("/"))
        response, payload = cls._request_json("GET", me_url, firebase_id_token, timeout=60)

        if response.status_code == 403 and payload.get("code") == "DEVELOPER_NOT_LINKED":
            return None
        if response.status_code >= 400:
            raise ToolError(
                "Failed to call /api/v1/developer/me ({}): {}".format(
                    response.status_code,
                    payload.get("error", response.text[:500]),
                )
            )
        return payload

    @classmethod
    def _create_developer(cls, api_base, firebase_id_token):
        create_url = "{}/api/v1/developer/create".format(api_base.rstrip("/"))
        response, payload = cls._request_json("POST", create_url, firebase_id_token, timeout=60, json_body={})
        if response.status_code >= 400:
            raise ToolError(
                "Failed to create developer ({}): {}".format(
                    response.status_code,
                    payload.get("error", response.text[:500]),
                )
            )

    @classmethod
    def _has_linked_developer(cls, me_payload):
        developer = (me_payload or {}).get("developer") or {}
        if not isinstance(developer, dict):
            return False
        return bool(developer.get("id") or developer.get("_id") or developer.get("firebase_uid"))

    @classmethod
    def _platform_from_capture_path(cls, path):
        basename = os.path.basename(path)
        parts = basename.split("_", 1)
        if len(parts) != 2 or not parts[0]:
            raise ToolError("Could not infer platform from capture filename: {}".format(path))
        return parts[0]

    @classmethod
    def _append_capture_files(cls, files_payload, capture_paths):
        open_handles = []
        for capture_path in capture_paths:
            platform = cls._platform_from_capture_path(capture_path)
            field_name = "screenshots_{}".format(platform)
            mime_type = mimetypes.guess_type(capture_path)[0] or "application/octet-stream"
            try:
                handle = open(capture_path, "rb")
            except OSError as e:
                raise ToolError("Could not open screenshot file {}: {}".format(capture_path, e))
            open_handles.append(handle)
            files_payload.append(
                (
                    field_name,
                    (os.path.basename(capture_path), handle, mime_type),
                )
            )
        return open_handles

    def _capture_with_emulator(self, args):
        gif_paths = []
        screenshot_paths = []
        screenshot_command = ScreenshotCommand()
        screenshot_command._set_debugging(args.v)
        random_start = "{:02d}:{:02d}:57".format(random.randint(0, 23), random.randint(0, 59))
        capture_args = argparse.Namespace(
            v=args.v,
            sdk=args.sdk,
            no_correction=False,
            scale=1,
            no_open=True,
            gif_fps=10,
            gif_start_time=random_start,
        )
        if args.capture_gif_all_platforms:
            self._step("Capturing GIF screenshots from emulator...")
            gif_paths = screenshot_command._capture_gif_all_platforms(capture_args)
        if args.capture_all_platforms:
            self._step("Capturing static screenshots from emulator...")
            screenshot_paths = screenshot_command._capture_all_platforms(capture_args)
        return gif_paths, screenshot_paths

    @classmethod
    def _validate_screenshot_assets(cls, gif_paths, screenshot_paths):
        if gif_paths or screenshot_paths:
            return
        raise ToolError(
            "No screenshots were collected. Screenshot upload is required for publish."
        )

    @classmethod
    def _prompt_local_screenshot_files(cls):
        print("{}{}Add local screenshot/GIF files (relative or absolute paths).{}".format(Style.BRIGHT, Fore.BLUE, Style.RESET_ALL))
        print("Filename should start with platform, e.g. emery_..., gabbro_...")
        print("Press Enter on an empty line when done.")
        gif_paths = []
        screenshot_paths = []
        while True:
            raw = input("File path: ").strip()
            if not raw:
                break
            path = os.path.abspath(raw)
            if not os.path.exists(path):
                print("{}File not found:{} {}".format(Fore.YELLOW, Style.RESET_ALL, path))
                continue
            _, ext = os.path.splitext(path)
            if ext.lower() == ".gif":
                gif_paths.append(path)
            else:
                screenshot_paths.append(path)
        return gif_paths, screenshot_paths

    def _collect_screenshot_assets(self, args, pbw_metadata, allow_skip=False):
        if args.non_interactive:
            gif_paths, screenshot_paths = self._capture_with_emulator(args)
            if not allow_skip:
                self._validate_screenshot_assets(gif_paths, screenshot_paths)
            return gif_paths, screenshot_paths

        self._step("Screenshots")
        print("Choose screenshot source:")
        print("  1) Auto-capture from emulator (default)")
        print("  2) Select local screenshot/GIF files")
        if allow_skip:
            print("  3) Do not upload new screenshots")
        while True:
            choice = input("Source [{}]: ".format("1-3" if allow_skip else "1-2")).strip() or "1"
            if choice == "1":
                gif_paths, screenshot_paths = self._capture_with_emulator(args)
                if not allow_skip:
                    self._validate_screenshot_assets(gif_paths, screenshot_paths)
                return gif_paths, screenshot_paths
            if choice == "2":
                gif_paths, screenshot_paths = self._prompt_local_screenshot_files()
                if gif_paths or screenshot_paths:
                    return gif_paths, screenshot_paths
                if allow_skip:
                    print("{}No files added. Choose option 3 to continue without screenshot uploads.{}".format(Fore.YELLOW, Style.RESET_ALL))
                    continue
                print("{}Please add at least one screenshot/GIF file.{}".format(Fore.YELLOW, Style.RESET_ALL))
                continue
            if allow_skip and choice == "3":
                return [], []
            print("{}Please choose {}.{}".format(Fore.YELLOW, "1, 2, or 3" if allow_skip else "1 or 2", Style.RESET_ALL))

    @classmethod
    def _lookup_app_id_case_insensitive(cls, app_lookup, pbw_uuid):
        if not pbw_uuid:
            return None
        pbw_uuid_norm = str(pbw_uuid).strip().lower()
        for key, value in (app_lookup or {}).items():
            if str(key).strip().lower() == pbw_uuid_norm:
                return value
        return None

    @classmethod
    def _is_screenshot_error(cls, response, payload):
        if response.status_code != 400:
            return False
        error_text = (payload.get("error") or response.text or "").lower()
        return "screenshot" in error_text

    @classmethod
    def _upload_release(
        cls,
        api_base,
        app_id,
        firebase_id_token,
        pbw_path,
        version,
        release_notes,
        is_published,
        gif_paths,
        screenshot_paths,
    ):
        url = "{}/api/dashboard/apps/{}/releases".format(api_base.rstrip("/"), app_id)
        form_data = {
            "version": version,
            "releaseNotes": release_notes or "",
            "isPublished": "true",
            "replaceScreenshots": "false",
        }

        files_payload = []
        open_files = []
        try:
            try:
                pbw_handle = open(pbw_path, "rb")
            except OSError as e:
                raise ToolError("Could not open PBW file {}: {}".format(pbw_path, e))
            open_files.append(pbw_handle)
            files_payload.append(
                (
                    "pbwFile",
                    (os.path.basename(pbw_path), pbw_handle, "application/octet-stream"),
                )
            )

            open_files.extend(cls._append_capture_files(files_payload, gif_paths))
            open_files.extend(cls._append_capture_files(files_payload, screenshot_paths))

            try:
                response = cls._post_with_wait_bar(
                    url=url,
                    headers={"Authorization": "Bearer {}".format(firebase_id_token)},
                    data=form_data,
                    files=files_payload,
                    timeout=300,
                    label="Waiting for release upload response",
                )
            except ToolError as e:
                raise ToolError("Release upload request failed: {}".format(e))
        finally:
            for handle in open_files:
                try:
                    handle.close()
                except Exception:
                    pass

        payload = {}
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if response.status_code == 403 and payload.get("code") == "DEVELOPER_NOT_LINKED":
            raise ToolError(
                "Firebase account is valid but not linked to a developer account in appstore. "
                "Run 'pebble publish' again to auto-create your developer account."
            )

        if response.status_code >= 400:
            if cls._is_screenshot_error(response, payload) and (gif_paths or screenshot_paths):
                print("{}Screenshot validation failed — retrying upload without screenshots...{}".format(
                    Fore.YELLOW, Style.RESET_ALL))
                print("  Server said: {}".format(payload.get("error", response.text[:200])))
                return cls._upload_release(
                    api_base=api_base, app_id=app_id,
                    firebase_id_token=firebase_id_token,
                    pbw_path=pbw_path, version=version,
                    release_notes=release_notes,
                    is_published=is_published,
                    gif_paths=[], screenshot_paths=[],
                )
            raise ToolError(
                "Release upload failed ({}): {}".format(
                    response.status_code,
                    payload.get("error", response.text[:500]),
                )
            )

        return payload

    @classmethod
    def _git_remote_source_url(cls):
        try:
            remote = subprocess.check_output(
                ["git", "config", "--get", "remote.origin.url"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except Exception:
            return ""

        if not remote:
            return ""
        if remote.endswith(".git"):
            remote = remote[:-4]

        if remote.startswith("git@") and ":" in remote:
            host_path = remote.split("@", 1)[1]
            host, path = host_path.split(":", 1)
            return "https://{}/{}".format(host, path)

        if remote.startswith("ssh://git@"):
            no_scheme = remote[len("ssh://git@") :]
            parts = no_scheme.split("/", 1)
            if len(parts) == 2:
                return "https://{}/{}".format(parts[0], parts[1])

        return remote

    @classmethod
    def _prompt_with_default(cls, label, default):
        prompt = "{}{}{}{} [{}] (Enter to keep): ".format(
            Style.BRIGHT, Fore.CYAN, label, Style.RESET_ALL, default if default else ""
        )
        value = input(prompt).strip()
        return value if value else default

    @classmethod
    def _prompt_required(cls, label):
        while True:
            value = input("{}{}{}: ".format(Style.BRIGHT + Fore.CYAN, label, Style.RESET_ALL)).strip()
            if value:
                return value
            print("{}This field is required.{}".format(Fore.YELLOW, Style.RESET_ALL))

    @classmethod
    def _normalize_category_value(cls, value):
        if not value:
            return None
        raw = str(value).strip().lower()
        aliases = {
            "tools-utilities": "tools",
            "tools_and_utilities": "tools",
            "tool": "tools",
            "health-fitness": "health",
            "health_and_fitness": "health",
            "fitness": "health",
            "notification": "notifications",
            "remote": "remotes",
            "game": "games",
        }
        normalized = aliases.get(raw, raw)
        allowed = {"daily", "tools", "notifications", "remotes", "health", "games"}
        if normalized in allowed:
            return normalized
        return raw

    @classmethod
    def _prompt_category_key(cls, me_payload, app_type):
        options = list((me_payload.get("app_category_options") or []))
        if app_type == "watchface":
            face_options = ((me_payload.get("category_options") or {}).get("watchface") or [])
            options = [
                {
                    "key": (item.get("slug") or item.get("name") or item.get("id") or "").strip().lower(),
                    "id": item.get("id"),
                    "name": item.get("name") or item.get("slug") or item.get("id"),
                }
                for item in face_options
            ]

        normalized_options = []
        for opt in options:
            display_name = opt.get("name") or opt.get("key") or opt.get("id")
            raw_value = opt.get("key") or opt.get("id") or opt.get("name")
            normalized = cls._normalize_category_value(raw_value)
            if not normalized:
                continue
            normalized_options.append({"name": display_name, "value": normalized})
        options = normalized_options
        default_key = "tools" if any(opt.get("value") == "tools" for opt in options) else (options[0].get("value") if options else "tools")

        if options:
            print("{}{}Choose a category (you can change this later).{}".format(Style.BRIGHT, Fore.BLUE, Style.RESET_ALL))
            for index, opt in enumerate(options, 1):
                print("  {}) {} ({})".format(index, opt.get("name") or opt.get("value"), opt.get("value")))

        while True:
            raw = input("Category [{}]: ".format(default_key)).strip()
            if not raw:
                return default_key
            if raw.isdigit() and options:
                idx = int(raw) - 1
                if 0 <= idx < len(options):
                    return options[idx]["value"]
                print("{}Invalid category index.{}".format(Fore.YELLOW, Style.RESET_ALL))
                continue
            return cls._normalize_category_value(raw)

    @classmethod
    def _prompt_new_app_details(cls, pbw_metadata, me_payload, default_version=None):
        print("{}{}You can change this later in the web dashboard.{}".format(Style.BRIGHT, Fore.BLUE, Style.RESET_ALL))

        name = cls._prompt_with_default("App name", pbw_metadata.get("app_name") or "Untitled App")
        version = cls._prompt_with_default("Version", default_version or pbw_metadata.get("version") or "1.0")
        description = cls._prompt_required("Short description")

        default_source = cls._git_remote_source_url()
        source = cls._prompt_with_default("Source URL", default_source)

        app_type = pbw_metadata.get("app_type") or "watchapp"
        category_key = None
        if app_type == "watchapp":
            category_key = cls._prompt_category_key(me_payload, app_type)

        icon_small = ""
        icon_large = ""
        if app_type == "watchapp":
            print("{}{}Do you have app icons already or would you like to automatically generate them?{}".format(
                Style.BRIGHT, Fore.BLUE, Style.RESET_ALL
            ))
            print("  1) I have icons ready")
            print("  2) No, please generate")
            while True:
                icon_choice = input("Icons [1-2]: ").strip() or "2"
                if icon_choice == "1":
                    icon_small = input("Path to iconSmall (80x80): ").strip()
                    icon_large = input("Path to iconLarge (144x144): ").strip()
                    break
                if icon_choice == "2":
                    break
                print("{}Please choose 1 or 2.{}".format(Fore.YELLOW, Style.RESET_ALL))

        return {
            "name": name,
            "version": version,
            "description": description,
            "source": source,
            "category": category_key,
            "icon_small_path": icon_small,
            "icon_large_path": icon_large,
        }

    @classmethod
    def _default_category_key(cls, me_payload, app_type):
        if app_type == "watchface":
            return None
        options = list((me_payload.get("app_category_options") or []))
        if app_type == "watchface":
            face_options = ((me_payload.get("category_options") or {}).get("watchface") or [])
            options = [
                {
                    "key": (item.get("slug") or item.get("name") or item.get("id") or "").strip().lower(),
                }
                for item in face_options
            ]
        normalized_options = [cls._normalize_category_value(opt.get("key") or opt.get("id")) for opt in options]
        normalized_options = [opt for opt in normalized_options if opt]
        if any(opt == "tools" for opt in normalized_options):
            return "tools"
        return normalized_options[0] if normalized_options else "tools"

    @classmethod
    def _collect_new_app_details(cls, args, pbw_metadata, me_payload, default_version=None):
        if not getattr(args, "non_interactive", False):
            return cls._prompt_new_app_details(pbw_metadata, me_payload, default_version=default_version)

        description = (getattr(args, "description", "") or "").strip()
        if not description:
            raise ToolError(
                "Creating a new app in --non-interactive mode requires --description."
            )

        default_name = pbw_metadata.get("app_name") or "Untitled App"
        default_version = default_version or pbw_metadata.get("version") or "1.0"
        default_source = cls._git_remote_source_url()
        app_type = pbw_metadata.get("app_type") or "watchapp"
        default_category = cls._default_category_key(me_payload, app_type)
        category = getattr(args, "category", None) or default_category or ""

        return {
            "name": (getattr(args, "name", None) or default_name).strip(),
            "version": (getattr(args, "version", None) or default_version).strip(),
            "description": description,
            "source": (getattr(args, "source", None) or default_source).strip(),
            "category": cls._normalize_category_value(category.strip().lower()) if category else None,
            "icon_small_path": (getattr(args, "icon_small", None) or "").strip(),
            "icon_large_path": (getattr(args, "icon_large", None) or "").strip(),
        }

    @classmethod
    def _create_app(
        cls,
        api_base,
        firebase_id_token,
        pbw_path,
        pbw_metadata,
        create_details,
        release_notes,
        is_published,
        gif_paths,
        screenshot_paths,
    ):
        url = "{}/api/dashboard/apps".format(api_base.rstrip("/"))
        form_data = {
            "name": create_details["name"],
            "type": pbw_metadata["app_type"],
            "version": create_details["version"],
            "expectedUuid": pbw_metadata["app_uuid"],
            "description": create_details["description"],
            "source": create_details["source"] or "",
            "releaseNotes": release_notes or "",
            "visible": "true",
            "isPublished": "true",
        }
        if create_details.get("category"):
            form_data["category"] = create_details["category"]
        auto_generate_icons = (
            pbw_metadata.get("app_type") == "watchapp"
            and not create_details.get("icon_small_path")
            and not create_details.get("icon_large_path")
        )
        if auto_generate_icons:
            form_data["iconPrompt"] = "{}: {}".format(
                create_details.get("name") or pbw_metadata.get("app_name") or "Pebble app",
                create_details.get("description") or "",
            ).strip()

        files_payload = []
        open_files = []
        try:
            try:
                pbw_handle = open(pbw_path, "rb")
            except OSError as e:
                raise ToolError("Could not open PBW file {}: {}".format(pbw_path, e))
            open_files.append(pbw_handle)
            files_payload.append(("pbwFile", (os.path.basename(pbw_path), pbw_handle, "application/octet-stream")))

            icon_small_path = create_details.get("icon_small_path")
            if icon_small_path:
                try:
                    icon_small_handle = open(icon_small_path, "rb")
                except OSError as e:
                    raise ToolError("Could not open iconSmall file {}: {}".format(icon_small_path, e))
                open_files.append(icon_small_handle)
                mime = mimetypes.guess_type(icon_small_path)[0] or "application/octet-stream"
                files_payload.append(("iconSmall", (os.path.basename(icon_small_path), icon_small_handle, mime)))

            icon_large_path = create_details.get("icon_large_path")
            if icon_large_path:
                try:
                    icon_large_handle = open(icon_large_path, "rb")
                except OSError as e:
                    raise ToolError("Could not open iconLarge file {}: {}".format(icon_large_path, e))
                open_files.append(icon_large_handle)
                mime = mimetypes.guess_type(icon_large_path)[0] or "application/octet-stream"
                files_payload.append(("iconLarge", (os.path.basename(icon_large_path), icon_large_handle, mime)))

            open_files.extend(cls._append_capture_files(files_payload, gif_paths))
            open_files.extend(cls._append_capture_files(files_payload, screenshot_paths))

            try:
                response = cls._post_with_wait_bar(
                    url=url,
                    headers={"Authorization": "Bearer {}".format(firebase_id_token)},
                    data=form_data,
                    files=files_payload,
                    timeout=300,
                    label="Waiting for app create response (icon generation may take ~2 min)",
                )
            except ToolError as e:
                raise ToolError("App create request failed: {}".format(e))
        finally:
            for handle in open_files:
                try:
                    handle.close()
                except Exception:
                    pass

        payload = {}
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if response.status_code >= 400:
            if cls._is_screenshot_error(response, payload) and (gif_paths or screenshot_paths):
                print("{}Screenshot validation failed — retrying upload without screenshots...{}".format(
                    Fore.YELLOW, Style.RESET_ALL))
                print("  Server said: {}".format(payload.get("error", response.text[:200])))
                return cls._create_app(
                    api_base=api_base,
                    firebase_id_token=firebase_id_token,
                    pbw_path=pbw_path,
                    pbw_metadata=pbw_metadata,
                    create_details=create_details,
                    release_notes=release_notes,
                    is_published=is_published,
                    gif_paths=[], screenshot_paths=[],
                )
            raise ToolError(
                "App create failed ({}): {}".format(
                    response.status_code,
                    payload.get("error", response.text[:500]),
                )
            )

        return payload

    @classmethod
    def _extract_app_id(cls, payload):
        if not isinstance(payload, dict):
            return None
        candidates = [
            payload.get("appId"),
            payload.get("app_id"),
            (payload.get("app") or {}).get("id") if isinstance(payload.get("app"), dict) else None,
            (payload.get("data") or {}).get("appId") if isinstance(payload.get("data"), dict) else None,
            (payload.get("data") or {}).get("app_id") if isinstance(payload.get("data"), dict) else None,
            ((payload.get("data") or {}).get("app") or {}).get("id")
            if isinstance((payload.get("data") or {}).get("app"), dict)
            else None,
        ]
        for candidate in candidates:
            if candidate:
                return str(candidate)
        return None

    def _print_upload_result(self, response_payload, app_id=None):
        message = response_payload.get("message") or "Publish completed successfully"
        self._ok(message)
        print("Visit the dashboard to add/edit your changelog: https://appstore-api.repebble.com/dashboard")
        if app_id:
            print("App page: https://apps.rePebble.com/{}".format(app_id))

        screenshot_results = response_payload.get("screenshotResults") or {}
        uploaded = screenshot_results.get("uploaded") or []
        failed = screenshot_results.get("failed") or []
        if uploaded:
            print("Uploaded screenshots: {}".format(len(uploaded)))
        if failed:
            print("Screenshot upload warnings: {}".format(len(failed)))
            for item in failed:
                print("  - [{}] {}: {}".format(item.get("platform", "unknown"), item.get("filename", "unknown"), item.get("error", "unknown error")))

    @classmethod
    def add_parser(cls, parser):
        parser = super(PublishCommand, cls).add_parser(parser)
        parser.add_argument("--sdk", nargs="?", help="SDK version to use, if not the currently selected one.")
        parser.add_argument(
            "--api-base",
            default=os.getenv("PEBBLE_APPSTORE_API_BASE", DEFAULT_APPSTORE_API_BASE),
            help="Appstore backend base URL (e.g. http://localhost:3000). Defaults to "
                 "PEBBLE_APPSTORE_API_BASE or {}.".format(DEFAULT_APPSTORE_API_BASE),
        )
        parser.add_argument("--release-notes", default="", help="Release notes text.")
        parser.add_argument("--is-published", action="store_true", default=False,
                            help="Create/publish release as visible immediately (default: false).")
        parser.set_defaults(capture_gif_all_platforms=True)
        parser.add_argument("--gif-all-platforms", action="store_true", dest="capture_gif_all_platforms",
                            help="Capture rollover GIFs for all supported platforms before upload (default: on).")
        parser.add_argument("--no-gif-all-platforms", action="store_false", dest="capture_gif_all_platforms",
                            help="Skip GIF capture before upload.")
        parser.add_argument("--all-platforms", action="store_true", dest="capture_all_platforms", default=False,
                            help="Capture static screenshots for all supported platforms before upload (default: off).")
        parser.add_argument("--firebase-id-token", default=None,
                            help="Firebase ID token to use directly (CI/non-interactive auth path). "
                                 "Defaults to PEBBLE_FIREBASE_ID_TOKEN when set.")
        parser.add_argument("--non-interactive", action="store_true", default=False,
                            help="Do not prompt during new-app creation; use flags/defaults instead (CI-friendly).")
        parser.add_argument("--name", default=None,
                            help="Override app name used when creating a new app.")
        parser.add_argument("--version", default=None,
                            help="Override version used when creating a new app.")
        parser.add_argument("--description", default=None,
                            help="Description used when creating a new app. Required with --non-interactive if app does not exist.")
        parser.add_argument("--source", default=None,
                            help="Override source URL used when creating a new app.")
        parser.add_argument("--category", default=None,
                            help="Override category key/id used when creating a new app.")
        parser.add_argument("--icon-small", default=None,
                            help="Path to iconSmall file used when creating a new app.")
        parser.add_argument("--icon-large", default=None,
                            help="Path to iconLarge file used when creating a new app.")
        return parser
