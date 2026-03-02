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

from cluster_state import ClusterState, Endpoint, NFSpec
from nf_launch_service import NFLaunchService, InstanceSpec

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
    self.cluster_state = ClusterState()
    self.launch_service = NFLaunchService(self.cluster_state)
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

  @route('launch_sfc', '/launch_sfc', methods=['PUT'])
  def launch_sfc(self, req, **kwargs):
    try:
      data = json.loads(req.body)
    except Exception:
      return Response(status=400, body="Invalid JSON")

    # chain_id must be an integer
    try:
      chain_id = int(data["chain_id"])
    except (KeyError, ValueError, TypeError):
      return Response(status=400, body="Missing or invalid 'chain_id' (must be integer)")

    # Every key other than chain_id is an NF type with a list of instance specs
    by_nf_type: dict = {}
    for key, value in data.items():
      if key == "chain_id":
        continue
      if not isinstance(value, list):
        return Response(status=400, body=f"Instances for '{key}' must be a JSON array")
      specs = []
      for entry in value:
        args = entry.get("args", [])
        ip_by_iface = entry.get("ip", {})
        specs.append(InstanceSpec(args=args, ip_by_iface=ip_by_iface))
      by_nf_type[key] = specs

    result = self.controller_app.launch_service.launch_instances(chain_id, by_nf_type)

    body = json.dumps({
      "launched": result.launched,
      "failed": result.failed,
    })

    if result.failed and not result.launched:
      status = 400
    elif result.failed:
      status = 207
    else:
      status = 200
    return Response(status=status, content_type="application/json", body=body)

 