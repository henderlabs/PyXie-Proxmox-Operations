"""A tiny stand-in for the Proxmox VE API, for the DEMO stack only.

The fictional demo cluster has no real PVE behind it. While it scores destinations, PyXie reads each VM's
config (CPU type, disks) and the cluster's HA rules; this answers those reads with plausible, invented values
so Balance Load can produce a real plan. It answers GET only and holds nothing worth protecting.

    python fake_pve.py 8006     # https on 0.0.0.0:8006 with a throwaway self-signed certificate
"""
import datetime, json, ssl, sys, tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


class H(BaseHTTPRequestHandler):
    def _send(self, data):
        body = json.dumps({"data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        parts = p.strip("/").split("/")
        if "/nodes/node-e/" in p + "/":  # the host that left the cluster does not answer, like a powered-off host
            body = b'{"data":null,"message":"hostname lookup failed: No route to host"}'
            self.send_response(595, "No route to host")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if "/nodes/node-e/" in p + "/":  # the host that left the cluster does not answer, like a powered-off host
            body = b'{"data":null,"message":"hostname lookup failed: No route to host"}'
            self.send_response(595, "No route to host")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if "qemu" in parts and p.endswith("/config"):
            vmid = parts[parts.index("qemu") + 1]
            return self._send({"cpu": "x86-64-v2-AES", "cores": 2, "memory": 4096, "scsi0": f"nas-nfs:vm-{vmid}-disk-0,size=32G"})
        if p.endswith("/config") and "nodes" in parts and "qemu" not in parts and "lxc" not in parts:
            return self._send({"description": "Rack 2, shelf 3", "wakeonlan": "aa:bb:cc:dd:ee:01", "startall-onboot-delay": "30", "digest": "d1"})
        if p.endswith("/status") and len(parts) >= 3 and parts[-3] == "nodes":
            return self._send({"cpu": 0.31, "uptime": 3300000, "loadavg": ["0.4", "0.5", "0.5"], "kversion": "Linux 6.8.12-4-pve", "pveversion": "pve-manager/8.4.1",
                               "memory": {"total": 137438953472, "used": 112742891520, "free": 24696061952}, "rootfs": {"total": 107374182400, "used": 21474836480, "free": 85899345920},
                               "cpuinfo": {"cpus": 16, "sockets": 1, "cores": 8, "model": "Example CPU"}, "boot-info": {"mode": "efi"}})
        if p.endswith("/rrddata"):
            import time as _t
            now = int(_t.time())
            return self._send([{"time": now - 60 * (60 - i), "cpu": 0.25 + 0.08 * ((i * 7) % 5) / 5, "iowait": 0.01, "loadavg": 0.4 + 0.1 * ((i * 3) % 4),
                                "memused": 112742891520, "memtotal": 137438953472, "netin": 2.0e6 + 4e5 * ((i * 5) % 6), "netout": 1.5e6 + 3e5 * ((i * 2) % 6)} for i in range(60)])
        if p.endswith("/dns"):
            return self._send({"dns1": "192.0.2.1", "dns2": "192.0.2.2", "search": "lab.example.test"})
        if p.endswith("/time"):
            return self._send({"timezone": "America/Denver", "time": 1793700000, "localtime": 1793678400})
        if p.endswith("/hosts"):
            return self._send({"data": "127.0.0.1 localhost.localdomain localhost\n192.0.2.11 node-a.lab.example.test node-a\n", "digest": "h1"})
        if p.endswith("/network"):
            return self._send([
                {"iface": "vmbr0", "type": "bridge", "cidr": "192.0.2.11/24", "address": "192.0.2.11", "netmask": "24", "gateway": "192.0.2.1",
                 "bridge_ports": "nic0", "bridge_stp": "off", "bridge_fd": "0", "autostart": 1, "active": 1, "families": ["inet"], "method": "static"},
                {"iface": "nic0", "type": "eth", "active": 1, "method": "manual", "families": ["inet"]},
            ])
        if p.endswith("/version"):
            return self._send({"version": "8.4.1", "release": "8.4"})
        self._send([])

    def log_message(self, *a):
        pass


def self_signed(dirpath):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "demo")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=2))
            .sign(key, hashes.SHA256()))
    with open(f"{dirpath}/k.pem", "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    with open(f"{dirpath}/c.pem", "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


if __name__ == "__main__":
    d = tempfile.mkdtemp()
    self_signed(d)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(f"{d}/c.pem", f"{d}/k.pem")
    srv = HTTPServer(("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 8006), H)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    srv.serve_forever()
