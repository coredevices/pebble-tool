

from six.moves import BaseHTTPServer
import logging
import os
import pyqrcode
import socket
import time
from six.moves.urllib import parse as urlparse
import webbrowser

from .phone_sensor import SENSOR_PAGE_HTML


logger = logging.getLogger("pebble_tool.util.browser")

class BrowserController(object):
    def __init__(self):
        self.port = None

    def open_config_page(self, url, callback):
        self.port = port = self._choose_port()
        url = self.url_append_params(url, {'return_to': 'http://localhost:{}/close?'.format(port)})
        webbrowser.open_new(url)
        self.serve_page(port, callback)

    def serve_page(self, port, callback):
        # This is an array so AppConfigHandler doesn't create an instance variable when trying to set the state to False
        running = [True]

        class AppConfigHandler(BaseHTTPServer.BaseHTTPRequestHandler):
            def do_GET(self):
                path, query = self.path.split('?', 1)
                if path == '/close':
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"OK")
                    running[0] = False
                    callback(query)
                else:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"Not Found")

        server = BaseHTTPServer.HTTPServer(('', port), AppConfigHandler)
        while running[0]:
            server.handle_request()

    def url_append_params(self, url, params):
        parsed = urlparse.urlparse(url, "http")
        query = parsed.query
        if parsed.query != '':
            query += '&'

        encoded_params = urlparse.urlencode(params)
        query += encoded_params
        return urlparse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, query, parsed.fragment))

    def serve_sensor_page(self, pypkjs_port, port=None):
        controller = self
        self.port = port or self._choose_port()

        class SensorPageHandler(BaseHTTPServer.BaseHTTPRequestHandler):
            PERMITTED_PATHS = {'static/js/backbone-min.js',
                               'static/js/backbone-min.map',
                               'static/js/propeller.min.js',
                               'static/js/sensors.js',
                               'static/js/underscore-min.js',
                               'static/js/underscore-min.map',
                               'static/js/websocket.js',
                               'static/compass-arrow.png',
                               'static/compass-rose.png',
                               'static/stylesheets/normalize.min.css',
                               'static/stylesheets/sensors.css'}

            def do_HEAD(self):
                self.send_response(200)
                self.end_headers()

            def do_GET(self):
                requested_file = self.path.rsplit('/', 1)[1]
                file_path = self.path.lstrip('/')
                if requested_file == '':
                    self.send_response(200)
                    self.send_header('Content-type', 'text/html')
                    self.end_headers()
                    self.wfile.write(SENSOR_PAGE_HTML.format(websocket_host="'{}'".format(controller._get_ip()),
                                                             websocket_port="'{}'".format(pypkjs_port)).encode())
                elif file_path in self.PERMITTED_PATHS:
                    try:
                        file_contents = open(os.path.join(os.path.dirname(os.path.realpath(__file__)), file_path))
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(file_contents.read().encode())
                    except IOError:
                        self.send_response(404)
                        self.end_headers()
                        self.wfile.write(b"Not Found")
                else:
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(b"Forbidden")

            def log_request(self, code='-', size='-'):
                logger.debug("{} - - [{}] '{}' {} {}".format(self.client_address[0], self.log_date_time_string(),
                                                               self.requestline, code, size))

        server = BaseHTTPServer.HTTPServer(('', self.port), SensorPageHandler)
        try:
            url = "http://{}:{}".format(self._get_ip(), server.server_port)
            url_code = pyqrcode.create(url)
            print((url_code.terminal(quiet_zone=1)))
            print("===================================================================================================")
            print(("Please scan the QR code or enter the following URL in your mobile browser:\n{}".format(url)))
            print("===================================================================================================")
        except socket.error:
            print(("Unable to determine local IP address. Please browse to port {} on this machine from your mobile "
                  "browser.".format(server.server_port)))

        print("\nUse Ctrl-C to stop sending sensor data to the emulator.\n")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("Stopping...")
            server.server_close()
            time.sleep(2) # Wait for WS connection to die between phone/QEMU

    def _choose_port(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('localhost', 0))
        addr, port = s.getsockname()
        s.close()
        return port

    @classmethod
    def _get_ip(cls):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))
        addr, port = s.getsockname()
        s.close()
        return addr
