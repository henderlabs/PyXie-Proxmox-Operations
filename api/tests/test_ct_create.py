from pyxie_core.ct_create import build_ct_params, valid_ssh_keys, validate_ct_spec

BASE = {"node": "n1", "hostname": "web01", "template": "local:vztmpl/alpine-3.22-default_20250617_amd64.tar.xz", "disk_storage": "local-lvm", "nic_bridge": "vmbr0"}
KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample user@host"


def ok(**kw):
    spec, errors, notes = validate_ct_spec({**BASE, **kw})
    assert errors == [], errors
    return spec, notes


def test_minimum_and_params():
    s, notes = ok()
    assert (s["cores"], s["memory_mb"], s["swap_mb"], s["disk_size_gb"], s["ip"]) == (1, 512, 512, 8, "dhcp") and any("no SSH key" in n for n in notes)
    p = build_ct_params(s, 200)
    assert p["rootfs"] == "local-lvm:8" and p["net0"] == "name=eth0,bridge=vmbr0,ip=dhcp" and p["unprivileged"] == 1 and p["ostemplate"] == BASE["template"]
    assert "ssh-public-keys" not in p and "password" not in p


def test_fixed_address_keys_tags_pool():
    s, notes = ok(ip="192.168.1.50/24", gateway="192.168.1.1", nic_vlan=20, nic_firewall=True, nameserver="1.1.1.1 9.9.9.9", searchdomain="lan", ssh_keys=KEY, tags=["web", "Web", "prod"],
                  pool="Prod", cores=2, memory_mb=1024, swap_mb=0, disk_size_gb=16, start_at_boot=True)
    p = build_ct_params(s, 201)
    assert p["net0"] == "name=eth0,bridge=vmbr0,ip=192.168.1.50/24,gw=192.168.1.1,tag=20,firewall=1"
    assert p["ssh-public-keys"] == KEY and p["tags"] == "web;prod" and p["pool"] == "Prod" and p["swap"] == 0 and p["onboot"] == 1 and p["nameserver"] == "1.1.1.1 9.9.9.9"
    assert notes == []
    s, notes = ok(ip="dhcp", gateway="10.0.0.1")
    assert s["gateway"] is None and any("gateway" in n for n in notes)


def test_validation_errors():
    bad = {"node": "", "hostname": "bad host!", "template": "local:iso/x.iso", "disk_storage": "", "nic_bridge": "", "cores": 0, "memory_mb": 1, "ip": "10.0.0.5", "nic_vlan": 9000,
           "ssh_keys": "not a key", "tags": ["a b"], "nameserver": "dns.example"}
    _, errors, _ = validate_ct_spec(bad)
    assert len(errors) >= 11
    assert valid_ssh_keys(KEY + "\n\n" + KEY)[0].count("\n") == 1
    assert validate_ct_spec({**BASE, "ip": "fe80::1/64"})[1]
