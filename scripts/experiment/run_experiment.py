#!/usr/bin/env python3
"""Run the phased experiment end to end and collect everything into data/experiment/<run>/.

Deploys the network, funds the actors, then keeps the miner and background traffic on while each
phase turns actors on or off by starting or deleting their commander pods. Turning an actor off
drops its raw P2P peers; its own node stays in the network like any other node.

Writes phases.csv (phase boundaries), pods.csv (every pod with its role, pod IP and, for tanks, the
Service IP that other tanks connect through, to classify peers in the logs), each commander's log,
every tank's debug.log and message capture, and copies of the network and params used.
"""

import argparse
import csv
import datetime
import gzip
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
# Importing from scenarios/ must not leave bytecode there: warnet bundles that directory into pods.
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE / "scenarios"))
import params  # noqa: E402
from traffic import sleep_until  # noqa: E402

NETWORK = HERE / "networks" / "cmpct"
ACTORS = ("mint", "fleet", "sweeper")
DATADIR = "/root/.bitcoin/regtest"


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="microseconds")


class Run:
    def __init__(self, out):
        self.out = out
        self.running = {}  # actor or service name -> pod
        self.pods = []  # rows of pods.csv
        self.phases = []  # rows of phases.csv
        self.deployed = False
        (out / "commanders").mkdir(parents=True)
        (out / "tanks").mkdir()
        self.logfile = open(out / "runner.log", "a")

    def log(self, msg):
        line = f"{utcnow()} {msg}"
        print(line, flush=True)
        self.logfile.write(line + "\n")
        self.logfile.flush()

    def sh(self, *cmd, check=True):
        return subprocess.run(cmd, check=check, capture_output=True, text=True, cwd=ROOT).stdout

    def tanks(self):
        fmt = "jsonpath={range .items[*]}{.metadata.name} {.status.podIP}{\"\\n\"}{end}"
        out = self.sh("kubectl", "get", "pods", "-l", "mission=tank", "-o", fmt)
        return dict(line.split() for line in out.splitlines() if line.strip())

    def deploy(self):
        if self.sh("helm", "list", "-n", "default", "-q").strip():
            raise SystemExit("the cluster is not empty; tear it down first")
        self.deployed = True
        self.log("deploying")
        self.sh("warnet", "deploy", str(NETWORK))
        self.sh("kubectl", "wait", "--for=condition=Ready", "pod", "-l", "mission=tank", "--timeout=600s")
        fmt = "jsonpath={range .items[*]}{.metadata.name} {.spec.clusterIP}{\"\\n\"}{end}"
        out = self.sh("kubectl", "get", "svc", "-o", fmt)
        services = dict(line.split() for line in out.splitlines() if line.strip())
        for name, ip in sorted(self.tanks().items()):
            role = "miner" if name.startswith("miner-") else "innocent" if name.startswith("node-") else "actor-node"
            self.pods.append({"pod": name, "role": role, "ip": ip, "service_ip": services.get(name, ""),
                              "started": utcnow(), "stopped": ""})

    def start(self, role, scenario, *args):
        out = self.sh("warnet", "run", str(HERE / "scenarios" / f"{scenario}.py"), *args)
        pod = re.search(r"commander-[a-z]+-\d+", out).group(0)
        self.sh("kubectl", "wait", "--for=condition=Ready", "pod", pod, "--timeout=300s", check=False)
        ip = self.sh("kubectl", "get", "pod", pod, "-o", "jsonpath={.status.podIP}", check=False)
        self.pods.append({"pod": pod, "role": role, "ip": ip, "service_ip": "", "started": utcnow(), "stopped": ""})
        self.running[role] = pod
        self.log(f"started {role} {pod} {ip}")
        return pod

    def wait_done(self, pod, timeout_s):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            logs = self.sh("kubectl", "logs", pod, "-c", "commander", check=False)
            if "Tests successful" in logs:
                return
            if "Traceback" in logs or "Test failed" in logs:
                raise SystemExit(f"{pod} failed, see its log")
            time.sleep(10)
        raise SystemExit(f"{pod} did not finish in {timeout_s}s")

    def stop(self, role):
        pod = self.running.pop(role)
        # A deleted pod takes its log with it, so save it first. A short grace period keeps the phase
        # boundary sharp; the actor has nothing to flush.
        self.save_log(pod)
        self.sh("kubectl", "delete", "pod", pod, "--grace-period=1", "--wait=true", check=False)
        for row in self.pods:
            if row["pod"] == pod:
                row["stopped"] = utcnow()
        self.log(f"stopped {role} {pod}")

    def save_log(self, pod):
        logs = self.sh("kubectl", "logs", pod, "-c", "commander", check=False)
        (self.out / "commanders" / f"{pod}.log").write_text(re.sub(r"\x1b\[[0-9;]*m", "", logs))

    def height(self):
        return self.sh("kubectl", "exec", "miner-0", "--", "bitcoin-cli", "-regtest", "getblockcount").strip()

    def phase(self, i, name, actors, seconds):
        for actor in ACTORS:
            if actor in self.running and actor not in actors:
                self.stop(actor)
        for actor in actors:
            if actor not in self.running:
                self.start(actor, actor)
        row = {"index": i, "phase": name, "actors": "+".join(actors)}
        row.update(start=utcnow(), start_height=self.height())
        self.log(f"phase {i} {name} [{row['actors']}] at height {row['start_height']}")
        sleep_until(time.time() + seconds)
        row.update(end=utcnow(), end_height=self.height())
        self.phases.append(row)
        self.write_tables()

    def write_tables(self):
        for name, rows in (("phases.csv", self.phases), ("pods.csv", self.pods)):
            if rows:
                with open(self.out / name, "w", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=list(rows[0]))
                    w.writeheader()
                    w.writerows(rows)

    def collect(self, capture):
        for role in list(self.running):
            self.stop(role)
        self.write_tables()
        for name in sorted(self.tanks()):
            self.log(f"collecting {name}")
            with gzip.open(self.out / "tanks" / f"{name}.debug.log.gz", "wb") as f:
                cmd = ["kubectl", "exec", name, "--", "cat", f"{DATADIR}/debug.log"]
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
                shutil.copyfileobj(proc.stdout, f)
                proc.wait()
            if capture:
                with open(self.out / "tanks" / f"{name}.message_capture.tar.gz", "wb") as f:
                    cmd = ["kubectl", "exec", name, "--", "tar", "czf", "-", "-C", DATADIR, "message_capture"]
                    subprocess.run(cmd, stdout=f, check=False)
        shutil.copy(NETWORK / "network.yaml", self.out)
        shutil.copy(NETWORK / "node-defaults.yaml", self.out)
        shutil.copy(HERE / "scenarios" / "params" / "__init__.py", self.out / "params.py")

    def teardown(self):
        self.log("tearing down")
        for release in self.sh("helm", "list", "-n", "default", "-q").split():
            self.sh("helm", "uninstall", "-n", "default", release, check=False)
        self.sh("kubectl", "delete", "pod", "-l", "mission=commander", "--wait=false", check=False)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", default=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M"))
    p.add_argument("--warmup", type=float, default=params.WARMUP_S, help="seconds of miner and background only")
    p.add_argument("--phase", type=float, default=params.PHASE_S, help="seconds per phase")
    p.add_argument("--no-capture", action="store_true", help="skip downloading message_capture")
    p.add_argument("--keep", action="store_true", help="leave the cluster up at the end")
    args = p.parse_args()
    os.environ["PATH"] = f"{ROOT / '.venv' / 'bin'}:{os.environ['PATH']}"

    run = Run(ROOT / "data" / "experiment" / args.name)
    try:
        run.deploy()
        bootstrap = run.start("bootstrap", "bootstrap")
        run.wait_done(bootstrap, 900)
        run.save_log(bootstrap)
        del run.running["bootstrap"]
        run.start("miner", "miner")
        run.start("background", "background")
        run.log(f"warmup {args.warmup}s")
        sleep_until(time.time() + args.warmup)
        for i, (name, actors) in enumerate(params.PHASES):
            run.phase(i, name, actors, args.phase)
    finally:
        if run.deployed:
            run.collect(not args.no_capture)
            if not args.keep:
                run.teardown()
        run.log("done")


if __name__ == "__main__":
    main()
