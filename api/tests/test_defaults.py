from pyxie_core.defaults import BUILTIN, deviations, layer_order, machine_matches, resolve, validate_scope, validate_values

C1, C2 = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"


def L(t, v=None, c=None, p=0, **values):
    return {"scope_type": t, "scope_value": v, "cluster_id": c, "priority": p, "values": values}


def test_builtin_when_nothing_is_set():
    vals, src = resolve([], cluster_id=C1)
    assert vals == BUILTIN and set(src.values()) == {"built-in"}


def test_layers_override_field_by_field():
    layers = [L("cluster", None, None, cpu_type="x86-64-v3", cores=2), L("cluster", None, C1, cpu_type="host"), L("tag", "windows", None, bios="ovmf", cores=4),
              L("pool", "prod", None, cores=8), L("template", "Win11", None, memory_mb=8192, cores=6)]
    v, s = resolve(layers, cluster_id=C1, tags=["windows"], pool="prod", template="Win11", cluster_names={C1: "949"})
    assert v["cpu_type"] == "host" and s["cpu_type"] == "cluster 949"      # cluster beats all-clusters
    assert v["cores"] == 6 and s["cores"] == "template Win11"               # template beats pool beats tag beats cluster
    assert v["bios"] == "ovmf" and s["bios"] == "tag windows"
    assert v["memory_mb"] == 8192 and v["scsihw"] == BUILTIN["scsihw"] and s["scsihw"] == "built-in"
    v2, _ = resolve(layers, cluster_id=C1, tags=["windows"], pool="prod")   # no template chosen
    assert v2["cores"] == 8 and v2["memory_mb"] == BUILTIN["memory_mb"]


def test_only_matching_layers_apply():
    layers = [L("cluster", None, C2, cpu_type="host"), L("tag", "linux", None, agent=True), L("pool", "dev", None, cores=3)]
    v, _ = resolve(layers, cluster_id=C1, tags=["windows"], pool="prod")
    assert v == BUILTIN                                                      # wrong cluster, tag and pool: nothing applies
    v, s = resolve(layers, cluster_id=C1, tags=["linux"], pool=None)
    assert v["agent"] is True and s["agent"] == "tag linux"


def test_tags_priority_decides_between_two_matching_tags():
    layers = [L("tag", "a", None, 1, cores=2), L("tag", "b", None, 5, cores=4)]
    assert resolve(layers, cluster_id=C1, tags=["a", "b"])[0]["cores"] == 4
    layers = [L("tag", "a", None, 9, cores=2), L("tag", "b", None, 5, cores=4)]
    assert resolve(layers, cluster_id=C1, tags=["a", "b"])[0]["cores"] == 2
    assert [l["scope_value"] for l in layer_order(layers)] == ["b", "a"]


def test_add_tags_accumulate_instead_of_override():
    layers = [L("cluster", None, None, add_tags=["managed"]), L("tag", "linux", None, add_tags=["Linux", "web"])]
    v, _ = resolve(layers, cluster_id=C1, tags=["linux"])
    assert v["add_tags"] == ["managed", "Linux", "web"]


def test_validation():
    ok, err = validate_values({"cores": 4, "machine": "q35", "agent": True, "nic_vlan": None, "add_tags": ["a", "A", "b"]})
    assert err == [] and ok == {"cores": 4, "machine": "q35", "agent": True, "add_tags": ["a", "b"]}
    _, err = validate_values({"cores": 0, "bios": "uefi", "nope": 1, "agent": "yes", "nic_model": "ne2k", "cpu_type": "bad type"})
    assert len(err) == 6
    assert validate_scope("cluster", None) is None and validate_scope("cluster", "x") is not None
    assert validate_scope("tag", "linux") is None and validate_scope("tag", "") is not None and validate_scope("pool", "bad;pool") is not None
    assert validate_scope("template", "Windows 11") is None and validate_scope("zone", "x") is not None


def test_machine_matching_and_deviations():
    assert machine_matches(None, "pc") and machine_matches("pc-i440fx-8.1", "pc") and not machine_matches("pc-q35-8.1", "pc")
    assert machine_matches("pc-q35-9.0", "q35") and machine_matches("pc-q35-9.0", "pc-q35-9.0") and not machine_matches("q35", "pc-q35-9.0")
    eff = {"machine": "q35", "cpu_type": "x86-64-v3", "bios": "seabios", "agent": True, "scsihw": "virtio-scsi-single", "nic_model": "virtio"}
    src = {"machine": "cluster 949", "cpu_type": "tag linux", "bios": "built-in", "agent": "cluster 949", "scsihw": "built-in", "nic_model": "cluster 949"}
    actual = {"machine": None, "cpu_type": "x86-64-v3", "bios": "ovmf", "agent": False, "scsihw": "lsi", "nic_model": ["virtio", "e1000"]}
    got = {d["field"]: d for d in deviations(actual, eff, src)}
    assert set(got) == {"machine", "agent", "nic_model"}                     # cpu matches; bios/scsihw defaults are only built-in so never flagged
    assert got["machine"]["source"] == "cluster 949" and got["agent"]["default"] is True


def test_os_scope_layers_between_cluster_and_tag():
    layers = [
        {"scope_type": "cluster", "scope_value": None, "cluster_id": None, "priority": 0, "values": {"cores": 2, "cpu_type": "host"}},
        {"scope_type": "os", "scope_value": "win11", "cluster_id": None, "priority": 0, "values": {"cores": 4, "bios": "ovmf"}},
        {"scope_type": "tag", "scope_value": "db", "cluster_id": None, "priority": 0, "values": {"cores": 8}},
    ]
    vals, src = resolve(layers, cluster_id="c", tags=[], ostype="win11")
    assert vals["cores"] == 4 and vals["bios"] == "ovmf" and vals["cpu_type"] == "host" and src["bios"] == "OS type win11"
    vals, _ = resolve(layers, cluster_id="c", tags=["db"], ostype="win11")
    assert vals["cores"] == 8 and vals["bios"] == "ovmf"
    vals, _ = resolve(layers, cluster_id="c", tags=[], ostype="l26")
    assert vals["cores"] == 2 and vals["bios"] == "seabios"
    assert validate_scope("os", "win11") is None and validate_scope("os", "") and validate_scope("os", "Win 11")
