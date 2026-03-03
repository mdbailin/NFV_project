# Contributors:
#   Branden Kretschmer:

import json

from os_ken.ofproto import ofproto_v1_3
from rest_router_start_server import rest_controller
from webob import Response
from wsgi import ControllerBase, WSGIApplication, route

from cluster_state import ClusterState, Endpoint, NFSpec

from nf_launch_service import NFLaunchService, InstanceSpec

# ====================================================================================================
# Global Variables
# ====================================================================================================
controller_instance_name = "nfv_manager_api"
url = "/nfv_manager/{method}"


# ====================================================================================================
# Controller code
# ====================================================================================================
class NFVController(rest_controller):
    """
    OS Ken controller for routing flows using NFVI Manager Cluster State.
    """

    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super(NFVController, self).__init__(*args, **kwargs)
        print("Initializing OSKen controller app")
        self.cluster_state = ClusterState()
        self.launch_service = NFLaunchService(self.cluster_state, logger=self.logger)
        wsgi = kwargs["wsgi"]
        wsgi.register(RESTLinkage, {controller_instance_name: self})

# ====================================================================================================
# Rest Linkage for the controller
# ====================================================================================================
class RESTLinkage(ControllerBase):
    """
    Class for Rest Linkage of the os_ken controller.
    Use self.controller_app to access controller member variables.
    """

    def __init__(self, req, link, data, **config):
        super(RESTLinkage, self).__init__(req, link, data, **config)
        self.controller_app = data[controller_instance_name]

    def _json(self, status_code, payload):
        return Response(
            status=status_code,
            content_type="application/json",
            body=json.dumps(payload).encode("utf-8"),
        )

    # ================================================================================================
    # Northbound request handling functions
    # ================================================================================================
    @route("nfv_manager", url, methods=["GET"])
    def _hello(self, req, **kwargs):
        """
        Example endpoint. Endpoint is accessible @ https://localhost/nfv_manager/hello
        The function name is appended to the url path
        """
        return Response(status=200, body="hello wsgi server")

    @route("register_sfc", "/register_sfc", methods=["PUT"])
    def register_sfc(self, req, **kwargs):
        try:
            data = req.json_body
        except Exception:
            return self._json(
                400, {"status": "error", "error": "Invalid or malformed JSON payload"}
            )

        required = ["nf_chain", "chain_id", "SRC", "DST"]
        missing = [k for k in required if k not in data]
        if missing:
            return self._json(
                400,
                {
                    "status": "error",
                    "error": f"Missing required top-level field(s): {missing}",
                },
            )

        nf_chain = data["nf_chain"]
        chain_id_raw = data["chain_id"]

        if (
            not isinstance(nf_chain, list)
            or not nf_chain
            or not all(isinstance(x, str) for x in nf_chain)
        ):
            return self._json(
                400,
                {
                    "status": "error",
                    "error": "nf_chain must be a non-empty list of strings",
                },
            )

        try:
            chain_id = int(chain_id_raw)
        except Exception:
            return self._json(
                400, {"status": "error", "error": "chain_id must be an integer"}
            )

        src, parse_err = self._parse_endpoint(data["SRC"], "SRC")
        if parse_err:
            return self._json(400, {"status": "error", "error": parse_err})

        dst, parse_err = self._parse_endpoint(data["DST"], "DST")
        if parse_err:
            return self._json(400, {"status": "error", "error": parse_err})

        nf_specs = {}
        for nf in nf_chain:
            if nf not in data:
                return self._json(
                    400,
                    {
                        "status": "error",
                        "error": f"Missing NF specification block for '{nf}'",
                    },
                )

            spec = data[nf]
            spec_req = ["image", "interfaces", "init_script"]
            m = [k for k in spec_req if k not in spec]
            if m:
                return self._json(
                    400,
                    {
                        "status": "error",
                        "error": f"NF '{nf}' missing required field(s): {m}",
                    },
                )

            if not isinstance(spec["interfaces"], list) or not all(
                isinstance(x, str) for x in spec["interfaces"]
            ):
                return self._json(
                    400,
                    {
                        "status": "error",
                        "error": f"NF '{nf}' interfaces must be a list of strings",
                    },
                )

            nf_specs[nf] = NFSpec(
                image=spec["image"],
                init_script=spec["init_script"],
                interfaces=spec["interfaces"],
            )

        cs = self.controller_app.cluster_state
        reg_err = cs.register_chain(
            chain_id=chain_id, nf_chain=nf_chain, src=src, dst=dst, nf_specs=nf_specs
        )
        if reg_err is not None:
            return self._json(400, {"status": "error", "error": reg_err})

        return self._json(200, {"status": "registered", "chain_id": chain_id})

    def _parse_endpoint(self, obj, name):
        ep_req = ["MAC", "IP", "SWITCH_DPID", "PORT"]
        m = [k for k in ep_req if k not in obj]
        if m:
            return None, f"{name} missing field(s): {m}"

        try:
            return (
                Endpoint(
                    mac=obj["MAC"],
                    ip=obj["IP"],
                    switch_dpid=int(obj["SWITCH_DPID"]),
                    port=int(obj["PORT"]),
                ),
                None,
            )
        except Exception:
            return (
                None,
                f"{name} has invalid types (SWITCH_DPID and PORT must be integers)",
            )

    @route('launch_sfc', '/launch_sfc', methods=['PUT'])
    def launch_sfc(self, req, **kwargs):
        try:
            data = json.loads(req.body)
        except Exception:
            return self._json(400, {"status": "error", "error": "Invalid JSON"})

        # chain_id must be an integer
        try:
            chain_id = int(data["chain_id"])
        except (KeyError, ValueError, TypeError):
            return self._json(400, {"status": "error", "error": "Missing or invalid 'chain_id' (must be integer)"})

        # Every key other than chain_id is an NF type with a list of instance specs
        by_nf_type: dict = {}
        for key, value in data.items():
            if key == "chain_id":
                continue
            if not isinstance(value, list):
                return self._json(400, {"status": "error", "error": f"Instances for '{key}' must be a JSON array"})
            specs = []
            for entry in value:
                args = entry.get("args", [])
                ip_by_iface = entry.get("ip", {})
                specs.append(InstanceSpec(args=args, ip_by_iface=ip_by_iface))
            by_nf_type[key] = specs

        result = self.controller_app.launch_service.launch_instances(chain_id, by_nf_type)

        if result.failed and not result.launched:
            status = 400
        elif result.failed:
            status = 207
        else:
            status = 200
        return self._json(status, {
            "launched": result.launched,
            "failed": result.failed,
        })

