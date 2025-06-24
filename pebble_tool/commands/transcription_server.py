__author__ = 'andrews'

from enum import IntEnum
from time import sleep
import threading
import re
import logging

from libpebble2.services.voice import *

from .base import PebbleCommand

logger = logging.getLogger("pebble_tool.commands.transcription_server")

mapping = {
    'connectivity': TranscriptionResult.FailNoInternet,
    'disabled': SetupResult.FailDisabled,
    'no-speech-detected': TranscriptionResult.FailSpeechNotRecognized,  # works because there's no mic on qemu.
}


class TranscriptionServer(PebbleCommand):
    ''' Starts a voice server listening for voice transcription requests from the app '''
    command = 'transcribe'

    def _send_result(self):
        self._voice_service.send_stop_audio()
        if isinstance(self._error, TranscriptionResult):
            result = self._error
        else:
            result = TranscriptionResult.Success
        self._voice_service.send_dictation_result(result=result, sentences=[self._words], app_uuid=self._app_uuid)

    def _handle_session_setup(self, app_uuid, encoder_info):
        RESULT_DELAY = 4

        if self._timer is not None:
            self._timer.cancel()
        self._app_uuid = app_uuid

        if isinstance(self._error, SetupResult):
            result = self._error
        else:
            result = SetupResult.Success
        self._voice_service.send_session_setup_result(result, self._app_uuid)
        if result == SetupResult.Success:
            self._timer = threading.Timer(RESULT_DELAY, self._send_result)
            self._timer.start()

    def _handle_audio_stop(self):
        if self._timer is not None:
            self._timer.cancel()
        self._send_result()

    def __call__(self, args):
        super(TranscriptionServer, self).__call__(args)
        if args.error is not None:
            self._error = mapping[args.error]
        else:
            self._error = None
        self._voice_service = VoiceService(self.pebble)
        self._timer = None

        # Separate the sentence into individual words. Punctuation marks are treated as words
        if args.transcription:
            stripped = [w.strip() for w in re.split(r'(\W)', args.transcription) if w.strip() != '']
            # prefix punctuation marks with backspace character
            self._words = [(z if re.match(r'\w', z) else '\b' + z) for z in stripped]
        else:
            self._words = []

        self._voice_service.register_handler("session_setup", self._handle_session_setup)
        self._voice_service.register_handler("audio_stop", self._handle_audio_stop)

        logger.debug("Transcription server listening")

        try:
            while True:
                    sleep(1)
        except KeyboardInterrupt:
            return

    @classmethod
    def add_parser(cls, parser):
        parser = super(TranscriptionServer, cls).add_parser(parser)
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument('transcription', nargs='?', type=str,
                            help="Transcribed message to send in the dictation result")
        group.add_argument('--error', type=str, nargs='?', choices=list(mapping.keys()),
                            help='Error code to respond with, if simulating a failure.')
        return parser
