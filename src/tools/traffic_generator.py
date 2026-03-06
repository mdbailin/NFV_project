#!/usr/bin/env python3
"""
Traffic Generator for NFV Project

Runs config-driven iperf3 traffic between Docker containers.
Reads a traffic profile specifying src/dst container pairs and
time-windowed flow counts, then launches the appropriate iperf3
flows concurrently for each window.

Config format (configs/traffic_profile.json):
{
    "profiles": [
        {
            "src_container": "src1",
            "dst_container": "dst",
            "dst_ip": "145.12.131.92",
            "flows": [
                { "start_time": 0, "end_time": 10, "num_flows": 5 },
                { "start_time": 10, "end_time": 30, "num_flows": 15 }
            ]
        }
    ]
}

Usage:
    python3 traffic_generator.py configs/traffic_profile.json
"""

import argparse
import json
import subprocess
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

IPERF_PORT = 5001

class TrafficGenerator:
    def __init__(self, config_path, log_dir="logs", logger=None, mode="auto"):
        self.config_path = config_path
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(exist_ok=True)
        self.logger = logger if logger is not None else print

        self.config = None
        self.flow_logs = []
        self.bandwidth_logs = []

        self.mode = mode

    def _load_config(self):
        try:
            with open(self.config_path, "r") as f:
                config = json.load(f)
        except FileNotFoundError:
            return None, f"Config file not found: {self.config_path}"
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON in config file: {e}"

        if "profiles" not in config:
            return None, "Config must have 'profiles' key"

        return config, None

    def _all_endpoints(self):
        all_hosts = set()
        for profile in self.config["profiles"]:
            all_hosts.add(profile["src_container"])
            all_hosts.add(profile["dst_container"])
        return all_hosts

    def _docker_running_names(self):
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return set()
        return set(result.stdout.strip().splitlines()) if result.stdout.strip() else set()

    def _netns_names(self):
        result = subprocess.run(["ip", "netns", "list"], capture_output=True, text=True)
        if result.returncode != 0:
            return set()
        names = set()
        for line in (result.stdout or "").splitlines():
            parts = line.strip().split()
            if parts:
                names.add(parts[0])
        return names

    def _auto_select_mode(self):
        endpoints = self._all_endpoints()
        docker_names = self._docker_running_names()
        netns_names = self._netns_names()

        docker_hits = len(endpoints & docker_names)
        netns_hits = len(endpoints & netns_names)

        if docker_hits == len(endpoints) and len(endpoints) > 0:
            return "docker"
        if netns_hits == len(endpoints) and len(endpoints) > 0:
            return "netns"

        if netns_hits > docker_hits:
            return "netns"
        if docker_hits > netns_hits:
            return "docker"

        return "netns"

    def _validate_hosts(self):
        endpoints = self._all_endpoints()

        if self.mode == "auto":
            self.mode = self._auto_select_mode()

        if self.mode == "docker":
            running = self._docker_running_names()
            missing = endpoints - running
            if missing:
                return False, f"Docker containers not running: {missing}"
            self.logger(f" All {len(endpoints)} endpoints validated as Docker containers")
            return True, None

        if self.mode == "netns":
            netns = self._netns_names()
            missing = endpoints - netns
            if missing:
                return False, f"Network namespaces not found: {missing}"
            self.logger(f" All {len(endpoints)} endpoints validated as Linux namespaces")
            return True, None

        return False, f"Unknown mode: {self.mode}"

    def _exec_in_host(self, host, args, detach=False, capture=True, text=True):
        if self.mode == "docker":
            cmd = ["docker", "exec"]
            if detach:
                cmd.append("-d")
            cmd += [host] + args
        else:
            cmd = ["ip", "netns", "exec", host] + args

        return subprocess.run(cmd, capture_output=capture, text=text)

    def _pgrep_iperf_server(self, host, port):
        pattern = f"iperf3.*-s.*-p {port}"
        res = self._exec_in_host(host, ["pgrep", "-f", pattern], capture=True, text=True)
        return res.returncode == 0

    def _kill_iperf_port(self, host, port):
        pattern = f"iperf3.*-p {port}"
        self._exec_in_host(host, ["pkill", "-f", pattern], capture=True, text=True)

    def _start_server(self, host, port):
        if self._pgrep_iperf_server(host, port):
            self.logger(f"  iperf3 server already running in {host}:{port}")
            return

        self._kill_iperf_port(host, port)
        time.sleep(0.2)

        if self.mode == "docker":
            self._exec_in_host(host, ["iperf3", "-s", "-p", str(port)], detach=True, capture=True, text=True)
        else:
            self._exec_in_host(host, ["iperf3", "-s", "-p", str(port), "-D"], detach=False, capture=True, text=True)

        time.sleep(0.4)
        if self._pgrep_iperf_server(host, port):
            self.logger(f"  Started iperf3 server in {host}:{port}")
        else:
            self.logger(f"  WARNING: iperf3 server may not have started in {host}:{port}")

    def _start_all_servers(self, server_set):
        self.logger("\n=== Starting iperf3 servers ===")
        threads = []
        for host, port in server_set:
            t = threading.Thread(target=self._start_server, args=(host, port))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        time.sleep(0.8)
        self.logger(f" {len(server_set)} servers started\n")

    def _expand_flow_windows(self, profile):
        tasks = []
        src = profile["src_container"]
        dst = profile["dst_container"]
        dst_ip = profile["dst_ip"]

        for window in profile["flows"]:
            start_time = window["start_time"]
            end_time = window["end_time"]
            num_flows = window["num_flows"]
            duration = end_time - start_time

            for i in range(num_flows):
                flow_info = {
                    "src_container": src,
                    "dst_container": dst,
                    "dst_ip": dst_ip,
                    "port": IPERF_PORT,
                    "duration": duration,
                    "begin": start_time,
                    "flow_index": i,
                }
                tasks.append((start_time, flow_info))
        return tasks

    def _create_flow_result(self, flow_info, start_ts, end_ts, actual_duration):
        return {
            "src_container": flow_info["src_container"],
            "dst_container": flow_info["dst_container"],
            "dst_ip": flow_info["dst_ip"],
            "port": flow_info["port"],
            "flow_index": flow_info["flow_index"],
            "duration_requested": flow_info["duration"],
            "duration_actual": actual_duration,
            "begin_time": flow_info["begin"],
            "start_timestamp": start_ts,
            "end_timestamp": end_ts,
            "success": False,
            "error": None,
        }

    def _parse_iperf_json(self, output):
        out = (output or "").strip()
        if not out.startswith("{"):
            msg = out.splitlines()[-1] if out else "Empty iperf output"
            return False, None, None, msg[:200]

        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return False, None, None, "Failed to parse iperf JSON output"

        if isinstance(data, dict) and data.get("error"):
            return False, None, None, str(data["error"])

        end = data.get("end", {}) if isinstance(data, dict) else {}
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

        intervals = []
        if isinstance(data, dict) and "intervals" in data:
            for interval in data["intervals"]:
                if "sum" in interval:
                    interval_data = interval["sum"]
                    intervals.append(
                        {
                            "start": interval_data.get("start", 0),
                            "end": interval_data.get("end", 0),
                            "bits_per_second": interval_data.get("bits_per_second", 0),
                            "bytes": interval_data.get("bytes", 0),
                        }
                    )

        return True, metrics, intervals, None

    def _run_flow(self, flow_info):
        src = flow_info["src_container"]
        dst = flow_info["dst_container"]
        dst_ip = flow_info["dst_ip"]
        port = flow_info["port"]
        duration = flow_info["duration"]
        begin_time = flow_info["begin"]
        flow_index = flow_info["flow_index"]

        cmd_args = [
            "iperf3",
            "-c",
            dst_ip,
            "-p",
            str(port),
            "-t",
            str(duration),
            "-J",
            "-i",
            "3",
        ]

        start_ts = time.time()
        self.logger(
            f"[{begin_time:>4.1f}s] Flow {flow_index} starting: {src} -> {dst} ({dst_ip}:{port}, {duration}s) [{self.mode}]"
        )

        result_proc = self._exec_in_host(src, cmd_args, detach=False, capture=True, text=True)
        output = result_proc.stdout
        if not (output or "").strip() and (result_proc.stderr or "").strip():
            output = result_proc.stderr

        end_ts = time.time()
        actual_duration = end_ts - start_ts

        result = self._create_flow_result(flow_info, start_ts, end_ts, actual_duration)
        success, metrics, intervals, error_msg = self._parse_iperf_json(output)

        if success:
            result["success"] = True
            result.update(metrics)
            mbps = metrics["bits_per_second"] / 1e6
            self.logger(f"   Flow {flow_index} complete: {src} -> {dst} - {mbps:.2f} Mbps")

            if intervals:
                self.bandwidth_logs.append(
                    {
                        "src_container": src,
                        "dst_container": dst,
                        "port": port,
                        "flow_index": flow_index,
                        "begin_time": begin_time,
                        "start_timestamp": start_ts,
                        "intervals": intervals,
                    }
                )
        else:
            result["error"] = error_msg
            if "parse" in (error_msg or ""):
                result["raw_output"] = (output or "")[:500]
            self.logger(f"   Flow {flow_index} failed: {src} -> {dst} - {error_msg}")

        self.flow_logs.append(result)

    def _schedule_flows(self):
        self.logger("\n=== Scheduling flows ===")
        events = []
        for profile in self.config["profiles"]:
            events.extend(self._expand_flow_windows(profile))

        events.sort(key=lambda x: x[0])

        server_set = set()
        for i, (_, flow_info) in enumerate(events):
            flow_info["port"] = IPERF_PORT + i
            server_set.add((flow_info["dst_container"], flow_info["port"]))

        self._start_all_servers(server_set)

        self.logger(f"Total flows scheduled: {len(events)}")
        for begin, flow in events:
            self.logger(
                f"  [{begin:>4.1f}s] {flow['src_container']} -> {flow['dst_container']} "
                f"({flow['dst_ip']}:{flow['port']}, {flow['duration']}s, flow {flow['flow_index']})"
            )

        self.logger("\n=== Running flows ===")
        start_time = time.time()
        threads = []

        for begin_time, flow_info in events:
            now = time.time() - start_time
            sleep_time = begin_time - now
            if sleep_time > 0:
                time.sleep(sleep_time)

            t = threading.Thread(target=self._run_flow, args=(flow_info,))
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        total_time = time.time() - start_time
        self.logger(f"\n All flows complete ({total_time:.1f}s total)")

    def _save_logs(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = self.log_dir / f"traffic_results_{timestamp}.jsonl"

        with open(log_file, "w") as f:
            for log in self.flow_logs:
                f.write(json.dumps(log) + "\n")

        self.logger(f"\n=== Results saved to {log_file} ===")

        if self.bandwidth_logs:
            bw_log_file = self.log_dir / f"perceived_bandwidth_{timestamp}.jsonl"
            with open(bw_log_file, "w") as f:
                for log in self.bandwidth_logs:
                    f.write(json.dumps(log) + "\n")
            self.logger(f"Perceived bandwidth logs saved to {bw_log_file}")

        total = len(self.flow_logs)
        successful = sum(1 for log in self.flow_logs if log["success"])
        failed = total - successful

        self.logger(f"Total flows: {total}")
        self.logger(f"Successful: {successful}")
        self.logger(f"Failed: {failed}")

        if failed > 0:
            self.logger("\nFailed flows:")
            for log in self.flow_logs:
                if not log["success"]:
                    self.logger(
                        f"  {log['src_container']} -> {log['dst_container']}:{log['port']} "
                        f"flow {log['flow_index']} - {log['error']}"
                    )

    def setup(self):
        self.config, error = self._load_config()
        if error:
            self.logger(f"\n Error loading config: {error}")
            return False

        success, error = self._validate_hosts()
        if not success:
            self.logger(f"\n Error: {error}")
            return False

        return True

    def run(self):
        self.logger(f"\n{'='*60}")
        self.logger("NFV Traffic Generator")
        self.logger(f"Config: {self.config_path}")
        self.logger(f"Mode: {self.mode}")
        self.logger(f"{'='*60}")

        if self.config is None:
            if not self.setup():
                return False

        self._schedule_flows()
        self._save_logs()

        self.logger(f"\n{'='*60}")
        self.logger("Traffic generation complete")
        self.logger(f"{'='*60}\n")
        return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate iperf3 traffic from a profile.")
    parser.add_argument("config", help="Path to traffic profile JSON file")
    parser.add_argument("--log-dir", default="logs", metavar="DIR", help="Directory to write result logs")
    parser.add_argument("--mode", default="auto", choices=["auto", "docker", "netns"], help="Execution mode")
    args = parser.parse_args()

    gen = TrafficGenerator(args.config, log_dir=args.log_dir, mode=args.mode)
    success = gen.run()
    sys.exit(0 if success else 1)
