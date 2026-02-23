# Contributors:
#   Branden Kretschmer:

from dis import Instruction
import re
import os_ken
from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller import event
from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from os_ken.controller.handler import set_ev_cls
from os_ken.ofproto import ofproto_v1_3
from os_ken.ofproto import ofproto_v1_3_parser
from os_ken.ofproto import ether
from rest_router_start_server import rest_controller
# from os_ken.lib.packet import packet, ethernet, ether_types, arp, ipv4
from os_ken.lib.packet.packet import Packet
from os_ken.lib.packet.ethernet import ethernet
from os_ken.lib.packet import ether_types
from os_ken.lib.packet.arp import arp
from os_ken.lib.packet.ipv4 import ipv4
from os_ken.lib.packet.tcp import tcp
from os_ken.lib import mac

from wsgi import ControllerBase
from webob import Response
from wsgi import WSGIApplication, route
from os_ken.lib import dpid as dpid_lib

import sys
import json
import subprocess
import signal
import threading
import os
import re

#====================================================================================================
# Global Variables
#====================================================================================================
controller_instance_name = 'nfv_manager_api'
url = '/nfv_manager/{method}'

#====================================================================================================
# Controller code
#====================================================================================================

class NFVController(rest_controller):
  '''
    OS Ken controller for routing flows using NFVI Manager Cluster State.
  '''
  OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
  _CONTEXTS = {'wsgi': WSGIApplication}

  def __init__(self, *args, **kwargs):
    super(NFVController, self).__init__(*args, **kwargs)
    print("Initializing OSKen controller app")
    wsgi = kwargs['wsgi']
    wsgi.register(RESTLinkage,
                  {controller_instance_name: self})
    
 
#====================================================================================================
# Rest Linkage for the controller
#====================================================================================================

class RESTLinkage(ControllerBase):
  '''
    Class for Rest Linkage of the os_ken controller.
    use self.controller_app to access any/all of the data structure member variables in the controller
  '''
  def __init__(self, req, link, data, **config):
    super(RESTLinkage, self).__init__(req, link, data, **config)
    self.controller_app = data[controller_instance_name]

  #====================================================================================================
  # Northbound request handling functions
  #====================================================================================================
  @route('nfv_manager', url, methods=['GET'])
  def _hello(self, req, **kwargs):
    '''
      Example endpoint. Endpoint is accessible @ https://localhost/nfv_manager/hello

      The function name is appended to the url path
    '''
    return Response(status=200, body="hello wsgi server")

 