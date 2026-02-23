#!/usr/bin/env python3

################### TODO update this for the new project.  Much of it can be re-used #######################

"""
Testing Service for SDN Project

Runs config-driven iperf tests with concurrent flows.
Based on patterns from SDN_Workshop_1/net_tests.py

Usage:
    from test_runner import TestRunner
    
    # After starting your Mininet network:
    runner = TestRunner(net, 'test_config.json')
    runner.run()
"""

import json
import time
import threading
from datetime import datetime
from pathlib import Path
from mininet.log import info


class TestRunner:
    def __init__(self, net, config_path, log_dir="logs", logger=None):
        self.net = net
        self.config_path = config_path
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.logger = logger if logger is not None else info
        
        self.config = None
        self.servers = {}  # (host, port) -> process
        self.flow_logs = []
        self.topology_events = []
        self.bandwidth_logs = []  # Time-series bandwidth from iperf intervals
        self.latency_logs = []  # Time-series latency from ping
        self.latency_tests = []  # Latency test configurations
        
    def _load_config(self):
        """Load and validate test configuration. Returns (config, error_msg)."""
        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)
        except FileNotFoundError:
            return None, f"Config file not found: {self.config_path}"
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON in config file: {e}"
        
        if "tests" not in config:
            return None, "Config must have 'tests' key"
        
        self.topology_events = config.get("topology_events", [])
        self.latency_tests = config.get("latency_tests", [])
        
        return config, None
    
    def _validate_hosts(self):
        """Check that all hosts in config exist in network. Returns (success, error_msg)."""
        all_hosts = set()
        for test in self.config["tests"]:
            all_hosts.add(test["server"])
            all_hosts.add(test["client"])
        
        net_hosts = {h.name for h in self.net.hosts}
        missing = all_hosts - net_hosts
        
        if missing:
            return False, f"Hosts not found in network: {missing}"
        
        self.logger(f" All {len(all_hosts)} hosts validated\n")
        return True, None
    
    def _start_server(self, host_name, port, protocol):
        """Start iperf server on a host."""
        host = self.net.get(host_name)
        
        # Kill any existing iperf on this port
        host.cmd(f"pkill -f 'iperf3.*-p {port}'")
        time.sleep(0.5)  # Give time for process to die

        cmd = f"iperf3 -s -p {port} > /tmp/iperf3_server_{host_name}_{port}.log 2>&1 &"
        
        host.cmd(cmd)
        
        # Verify server started
        time.sleep(0.5)
        check = host.cmd(f"pgrep -f 'iperf3.*-s.*-p {port}'")
        if check.strip():
            self.logger(f"  Started iperf server on {host_name}:{port}\n")
        else:
            self.logger(f"  WARNING: iperf server may not have started on {host_name}:{port}\n")
            self.logger(f"    Check /tmp/iperf3_server_{host_name}_{port}.log for errors\n")
        
        return host
    
    def _start_all_servers(self):
        """Start all required iperf servers in parallel."""
        self.logger("\n=== Starting iperf servers ===\n")
        
        server_set = set()
        for test in self.config["tests"]:
            server_set.add((test["server"], test["port"], test.get("protocol", "udp")))
        
        threads = []
        for server, port, protocol in server_set:
            t = threading.Thread(target=self._start_server, args=(server, port, protocol))
            t.start()
            threads.append(t)
        
        for t in threads:
            t.join()
        
        # Give servers time to fully initialize
        time.sleep(2)
        self.logger(f" {len(server_set)} servers started\n\n")
    
    def _create_flow_result(self, flow_info, start_ts, end_ts, actual_duration):
        """Create initial result dictionary for a flow."""
        return {
            "server": flow_info["server"],
            "client": flow_info["client"],
            "port": flow_info["port"],
            "protocol": flow_info.get("protocol", "udp"),
            "parallel_streams": flow_info.get("parallel", 1),
            "bandwidth_requested": flow_info["bandwidth"],
            "duration_requested": flow_info["end"] - flow_info["begin"],
            "duration_actual": actual_duration,
            "begin_time": flow_info["begin"],
            "start_timestamp": start_ts,
            "end_timestamp": end_ts,
            "success": False,
            "error": None
        }
    
    def _parse_iperf_json(self, output, protocol):
        """
        Parse iperf3 output.
        Returns (success, metrics_dict, intervals_list, error_msg)
        """
        out = (output or "").strip()

        # If iperf printed plain-text error (common), surface it directly
        if not out.startswith("{"):
            # keep it short but useful
            msg = out.splitlines()[-1] if out else "Empty iperf output"
            return False, None, None, msg[:200]

        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return False, None, None, "Failed to parse iperf JSON output"

        if isinstance(data, dict) and data.get("error"):
            return False, None, None, str(data["error"])

        end = data.get("end", {}) if isinstance(data, dict) else {}

        # Try multiple places iperf3 stores summary
        summary = {}
        for key in ("sum_received", "sum", "sum_sent"):
            if isinstance(end.get(key), dict) and end[key]:
                summary = end[key]
                break

        bps = summary.get("bits_per_second", 0) or 0
        bytes_tx = summary.get("bytes", 0) or 0

        metrics = {
            "bytes_transferred": bytes_tx,
            "bits_per_second": bps,
        }

        # UDP loss fields (if present)
        if protocol == "udp":
            metrics["lost_packets"] = summary.get("lost_packets", 0) or 0
            metrics["packets"] = summary.get("packets", 0) or 0
            metrics["lost_percent"] = summary.get("lost_percent", 0) or 0

        # Extract interval data for time-series bandwidth
        intervals = []
        if isinstance(data, dict) and "intervals" in data:
            for interval in data["intervals"]:
                if "sum" in interval:
                    interval_data = interval["sum"]
                    intervals.append({
                        "start": interval_data.get("start", 0),
                        "end": interval_data.get("end", 0),
                        "bits_per_second": interval_data.get("bits_per_second", 0),
                        "bytes": interval_data.get("bytes", 0),
                    })
                    if protocol == "udp":
                        intervals[-1]["lost_packets"] = interval_data.get("lost_packets", 0)
                        intervals[-1]["packets"] = interval_data.get("packets", 0)

        return True, metrics, intervals, None

    def _ensure_server_running(self, server_name, port):
        server = self.net.get(server_name)

        # If already listening, do nothing
        listening = server.cmd(f"bash -lc \"ss -lntp 2>/dev/null | grep -E ':{port}\\b' | grep -q iperf3; echo $?\"").strip()
        if listening == "0":
            return

        # Kill any stale iperf3 and start a fresh one-shot server
        server.cmd(f"pkill -f \"iperf3.*-s.*-p {port}\" || true")
        server.cmd(f"iperf3 -s -p {port} -1 > /tmp/iperf3_server_{server_name}_{port}.log 2>&1 &")

        # Wait up to ~2s for it to listen
        for _ in range(20):
            ok = server.cmd(f"bash -lc \"ss -lntp 2>/dev/null | grep -E ':{port}\\b' | grep -q iperf3; echo $?\"").strip()
            if ok == "0":
                return
            time.sleep(0.1)

        self.logger(f"  WARNING: iperf server not listening on {server_name}:{port} (check /tmp/iperf3_server_{server_name}_{port}.log)\n")

    def _run_flow(self, flow_info):
        """Execute a single iperf flow."""
        server_name = flow_info["server"]
        client_name = flow_info["client"]
        port = flow_info["port"]
        protocol = flow_info.get("protocol", "udp")
        bandwidth = flow_info["bandwidth"]
        duration = flow_info["end"] - flow_info["begin"]
        begin_time = flow_info["begin"]
        parallel = flow_info.get("parallel", 1)
        
        server = self.net.get(server_name)
        client = self.net.get(client_name)

        #self._ensure_server_running(server_name, port)
        
        # Build iperf command
        cmd_parts = [
            "iperf3",
            f"-c {server.IP()}",
            f"-p {port}",
            f"-t {duration}",
            "--json",
            "--forceflush",
            "--interval 3",  # 1-second interval reporting for time-series data
        ]
        
        if parallel > 1:
            cmd_parts.append(f"-P {parallel}")
        
        if protocol == "udp":
            cmd_parts.extend(["-u", f"-b {bandwidth}M"])
        
        cmd = " ".join(cmd_parts)
        
        start_ts = time.time()
        parallel_str = f", {parallel} streams" if parallel > 1 else ""
        self.logger(f"[{begin_time:>4.1f}s] Flow starting: {client_name} -> {server_name}:{port} ({protocol}, {bandwidth}Mbps, {duration}s{parallel_str})\n")
        
        # Run command and capture output
        output = client.cmd(cmd)
        
        end_ts = time.time()
        actual_duration = end_ts - start_ts
        
        result = self._create_flow_result(flow_info, start_ts, end_ts, actual_duration)        
        success, metrics, intervals, error_msg = self._parse_iperf_json(output, protocol)
        
        if success:
            result["success"] = True
            result.update(metrics)
            mbps = metrics["bits_per_second"] / 1e6
            self.logger(f"   Flow complete: {client_name} -> {server_name} - {mbps:.2f} Mbps\n")
            
            # Log interval data for time-series bandwidth analysis
            if intervals:
                bandwidth_log = {
                    "server": server_name,
                    "client": client_name,
                    "port": port,
                    "protocol": protocol,
                    "begin_time": begin_time,
                    "start_timestamp": start_ts,
                    "intervals": intervals
                }
                self.bandwidth_logs.append(bandwidth_log)
        else:
            result["error"] = error_msg
            if error_msg == "Failed to parse iperf output":
                result["raw_output"] = output[:500]
            self.logger(f"   Flow failed: {client_name} -> {server_name} - {error_msg}\n")
        
        self.flow_logs.append(result)
    
    def _parse_ping_output(self, output):
        """
        Parse ping output to extract RTT measurements.
        Returns list of (timestamp, rtt_ms) tuples.
        """
        import re
        latencies = []
        
        # Pattern: time=X.XX ms or time=X ms
        pattern = r'time=([\d.]+)\s*ms'
        
        for line in output.splitlines():
            match = re.search(pattern, line)
            if match:
                rtt = float(match.group(1))
                latencies.append(rtt)
        
        return latencies
    
    def _run_latency_test(self, latency_info):
        """Execute a continuous ping test for latency measurement."""
        src_name = latency_info["src"]
        dst_name = latency_info["dst"]
        duration = latency_info["end"] - latency_info["begin"]
        interval = latency_info.get("interval", 1.0)
        begin_time = latency_info["begin"]
        
        src_host = self.net.get(src_name)
        dst_host = self.net.get(dst_name)
        
        # Calculate number of pings
        count = int(duration / interval)
        if count < 1:
            count = 1
        
        start_ts = time.time()
        self.logger(f"[{begin_time:>4.1f}s] Latency test starting: {src_name} -> {dst_name} (interval={interval}s, duration={duration}s)\n")
        
        # Run ping with specified interval using popen for thread-safety
        cmd = f"ping -i {interval} -c {count} {dst_host.IP()}"
        proc = src_host.popen(cmd, shell=True, stdout=-1, stderr=-1, text=True)
        output, _ = proc.communicate()
        
        end_ts = time.time()
        actual_duration = end_ts - start_ts
        
        # Parse RTT values
        rtt_values = self._parse_ping_output(output)
        
        # Calculate statistics
        if rtt_values:
            min_rtt = min(rtt_values)
            max_rtt = max(rtt_values)
            avg_rtt = sum(rtt_values) / len(rtt_values)
            
            # Calculate standard deviation
            variance = sum((x - avg_rtt) ** 2 for x in rtt_values) / len(rtt_values)
            stddev_rtt = variance ** 0.5
            
            self.logger(f"   Latency test complete: {src_name} -> {dst_name} - "
                       f"min={min_rtt:.2f}ms avg={avg_rtt:.2f}ms max={max_rtt:.2f}ms\n")
            
            # Create time-aligned latency samples (approximate timestamps)
            latency_samples = []
            for i, rtt in enumerate(rtt_values):
                sample_time = start_ts + (i * interval)
                latency_samples.append({
                    "relative_time": begin_time + (i * interval),
                    "timestamp": sample_time,
                    "rtt_ms": rtt
                })
            
            # Log the latency results
            latency_log = {
                "src": src_name,
                "dst": dst_name,
                "begin_time": begin_time,
                "start_timestamp": start_ts,
                "end_timestamp": end_ts,
                "duration_requested": duration,
                "duration_actual": actual_duration,
                "interval": interval,
                "count": len(rtt_values),
                "min_rtt_ms": min_rtt,
                "max_rtt_ms": max_rtt,
                "avg_rtt_ms": avg_rtt,
                "stddev_rtt_ms": stddev_rtt,
                "samples": latency_samples,
                "success": True
            }
            self.latency_logs.append(latency_log)
        else:
            self.logger(f"   Latency test failed: {src_name} -> {dst_name} - No ping responses\n")
            latency_log = {
                "src": src_name,
                "dst": dst_name,
                "begin_time": begin_time,
                "start_timestamp": start_ts,
                "end_timestamp": end_ts,
                "duration_requested": duration,
                "duration_actual": actual_duration,
                "interval": interval,
                "error": "No ping responses received",
                "success": False
            }
            self.latency_logs.append(latency_log)
    
    def _apply_topology_event(self, event):
        """Apply a topology event (link/switch/host up/down)."""
        event_type = event.get("type")
        action = event.get("action")
        
        if event_type == "link":
            node1, node2 = event.get("node1"), event.get("node2")
            self.logger(f"   Topology event: link {node1} <-> {node2} {action.upper()}\n")
            self.net.configLinkStatus(node1, node2, action)
            
        elif event_type == "switch":
            switch = event.get("switch")
            self.logger(f"   Topology event: switch {switch} {action.upper()}\n")
            sw = self.net.get(switch)
            if sw:
                if action == "down":
                    sw.stop()
                elif action == "up":
                    controllers = [c for c in self.net.controllers]
                    if controllers:
                        sw.start(controllers)
                    
        elif event_type == "host":
            host = event.get("host")
            self.logger(f"   Topology event: host {host} {action.upper()}\n")
            h = self.net.get(host)
            if h:
                h.intf().ifconfig(action)
        else:
            self.logger(f"   Unknown topology event type: {event_type}\n")
    
    def _schedule_topology_events(self, start_time):
        """Schedule topology events in background threads."""
        if not self.topology_events:
            return
        
        self.logger(f"\n=== Topology events scheduled: {len(self.topology_events)} ===\n")
        for event in self.topology_events:
            time_offset = event.get("time", 0)
            event_type = event.get("type", "")
            action = event.get("action", "")
            self.logger(f"  [{time_offset:>4.1f}s] {event_type} {action}\n")
        
        def run_event(event):
            event_time = event.get("time", 0)
            now = time.time() - start_time
            sleep_time = event_time - now
            if sleep_time > 0:
                time.sleep(sleep_time)
            self._apply_topology_event(event)
        
        threads = []
        for event in self.topology_events:
            t = threading.Thread(target=run_event, args=(event,))
            t.start()
            threads.append(t)
        
        return threads

    def _schedule_flows(self):
        """Schedule all flows based on begin/end times."""
        self.logger("\n=== Scheduling flows ===\n")
        
        # Build event list
        events = []
        for test in self.config["tests"]:
            server = test["server"]
            client = test["client"]
            port = test["port"]
            protocol = test.get("protocol", "udp")
            
            for window in test["test"]:
                flow_info = {
                    "server": server,
                    "client": client,
                    "port": port,
                    "protocol": protocol,
                    "bandwidth": window["bandwidth"],
                    "begin": window["begin"],
                    "end": window["end"],
                    "parallel": window.get("parallel", 1)
                }
                events.append((window["begin"], flow_info))
        
        # Sort by begin time
        events.sort(key=lambda x: x[0])
        
        self.logger(f"Total flows scheduled: {len(events)}\n")
        for begin, flow in events:
            parallel_str = f", {flow['parallel']} streams" if flow['parallel'] > 1 else ""
            self.logger(f"  [{begin:>4.1f}s] {flow['client']} -> {flow['server']}:{flow['port']} ({flow['protocol']}, {flow['bandwidth']}Mbps, {flow['end']-flow['begin']}s{parallel_str})\n")
        
        # Execute flows
        self.logger("\n=== Running tests ===\n")
        start_time = time.time()
        
        # Start topology events scheduler
        topo_threads = self._schedule_topology_events(start_time)
        
        threads = []
        
        for begin_time, flow_info in events:
            # Wait until begin time
            now = time.time() - start_time
            sleep_time = begin_time - now
            if sleep_time > 0:
                time.sleep(sleep_time)
            
            t = threading.Thread(target=self._run_flow, args=(flow_info,))
            t.start()
            threads.append(t)
        
        # Schedule latency tests
        if self.latency_tests:
            self.logger(f"\n=== Scheduling latency tests: {len(self.latency_tests)} ===\n")
            for latency_test in self.latency_tests:
                src = latency_test.get("src", "")
                dst = latency_test.get("dst", "")
                begin = latency_test.get("begin", 0)
                end = latency_test.get("end", 10)
                interval = latency_test.get("interval", 1.0)
                
                self.logger(f"  [{begin:>4.1f}s] {src} -> {dst} (interval={interval}s, duration={end-begin}s)\n")
                
                latency_info = {
                    "src": src,
                    "dst": dst,
                    "begin": begin,
                    "end": end,
                    "interval": interval
                }
                
                # Schedule latency test
                def schedule_latency(lat_info, start_t):
                    now = time.time() - start_t
                    sleep_time = lat_info["begin"] - now
                    if sleep_time > 0:
                        time.sleep(sleep_time)
                    self._run_latency_test(lat_info)
                
                t = threading.Thread(target=schedule_latency, args=(latency_info, start_time))
                t.start()
                threads.append(t)
        
        # Wait for all flow threads
        for t in threads:
            t.join()
        
        # Wait for topology event threads
        if topo_threads:
            for t in topo_threads:
                t.join()
        
        total_time = time.time() - start_time
        self.logger(f"\n All flows complete ({total_time:.1f}s total)\n")
    
    def _save_logs(self):
        """Save flow results to JSON lines file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = self.log_dir / f"test_results_{timestamp}.jsonl"
        
        with open(log_file, 'w') as f:
            for log in self.flow_logs:
                f.write(json.dumps(log) + '\n')
        
        self.logger(f"\n=== Results saved to {log_file} ===\n")
        
        # Save perceived bandwidth logs (time-series from iperf intervals)
        if self.bandwidth_logs:
            bw_log_file = self.log_dir / f"perceived_bandwidth_{timestamp}.jsonl"
            with open(bw_log_file, 'w') as f:
                for log in self.bandwidth_logs:
                    f.write(json.dumps(log) + '\n')
            self.logger(f"Perceived bandwidth logs saved to {bw_log_file}\n")
        
        # Save perceived latency logs (time-series from ping)
        if self.latency_logs:
            lat_log_file = self.log_dir / f"perceived_latency_{timestamp}.jsonl"
            with open(lat_log_file, 'w') as f:
                for log in self.latency_logs:
                    f.write(json.dumps(log) + '\n')
            self.logger(f"Perceived latency logs saved to {lat_log_file}\n")
        
        total = len(self.flow_logs)
        successful = sum(1 for log in self.flow_logs if log["success"])
        failed = total - successful
        
        self.logger(f"Total flows: {total}\n")
        self.logger(f"Successful: {successful}\n")
        self.logger(f"Failed: {failed}\n")
        
        if self.latency_logs:
            lat_total = len(self.latency_logs)
            lat_successful = sum(1 for log in self.latency_logs if log["success"])
            self.logger(f"Total latency tests: {lat_total}\n")
            self.logger(f"Successful latency tests: {lat_successful}\n")
        
        if failed > 0:
            self.logger("\nFailed flows:\n")
            for log in self.flow_logs:
                if not log["success"]:
                    self.logger(f"  {log['client']} -> {log['server']}:{log['port']} - {log['error']}\n")
    
    def setup(self):
        """Load config, validate hosts, discover hosts, and start servers. Run this before CLI."""
        self.config, error = self._load_config()
        if error:
            self.logger(f"\n Error loading config: {error}\n")
            return False
        
        success, error = self._validate_hosts()
        if not success:
            self.logger(f"\n Error: {error}\n")
            return False
        
        # # Trigger host discovery by pinging all hosts
        # self.logger("\n=== Triggering host discovery ===\n")
        # self.net.pingAll()
        # time.sleep(1)  # Give controller time to process
        
        self._start_all_servers()
        return True
    
    def run(self):
        """Execute full test suite. Returns True on success, False on error."""
        self.logger(f"\n{'='*60}\n")
        self.logger(f"SDN Project Test Runner\n")
        self.logger(f"Config: {self.config_path}\n")
        self.logger(f"{'='*60}\n")
        
        # If config not loaded yet, do full setup
        if self.config is None:
            if not self.setup():
                return False
        
        self._schedule_flows()
        self._save_logs()
        
        self.logger(f"\n{'='*60}\n")
        self.logger("Test run complete\n")
        self.logger(f"{'='*60}\n\n")
        return True
