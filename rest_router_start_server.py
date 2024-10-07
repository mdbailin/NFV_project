# Contributors
#   Branden Kretschmer (bkretschmer3@gatech.edu)

from rest_router import RestRouterAPI
from wsgi import WSGIServer, WSGIApplication, route
import threading

class rest_controller(RestRouterAPI):
    server: WSGIServer
    server_thread: threading.Thread
    def __init__(self, *args, **kwargs):
        super(rest_controller, self).__init__(*args, **kwargs)
        self.server = WSGIServer(self.application)
        self.server_thread = threading.Thread(target = lambda server: server.serve_forever(), args=(self.server,))
        self.server_thread.start()
