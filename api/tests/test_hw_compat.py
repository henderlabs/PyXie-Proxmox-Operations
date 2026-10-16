from pyxie_core.hw_compat import check_cpu_type, check_machine, split_cpu, valid_cpu_type, valid_machine, vendor_of

INTEL_NEW = {"name": "n1", "is_current": True, "cpu_model": "Intel(R) Xeon(R) Gold 6130", "flags": {"cx16", "lahf_lm", "popcnt", "pni", "sse4_1", "sse4_2", "ssse3", "avx", "avx2", "bmi1", "bmi2", "f16c", "fma", "abm", "movbe", "xsave", "aes", "avx512f", "avx512bw", "avx512cd", "avx512dq", "avx512vl"}, "models": {"x86-64-v3": "default", "x86-64-v2-AES": "default", "host": "default", "Skylake-Server": "Intel", "EPYC": "AMD"}}
INTEL_OLD = {"name": "n2", "is_current": False, "cpu_model": "Intel(R) Xeon(R) E5-2650", "flags": {"cx16", "lahf_lm", "popcnt", "pni", "sse4_1", "sse4_2", "ssse3", "avx", "aes", "xsave"}, "models": {"x86-64-v3": "default", "x86-64-v2": "default", "x86-64-v2-AES": "default", "host": "default"}}


def lv(rows):
    return {r["node"]: r["level"] for r in rows}


def test_split_cpu_and_validators():
    assert split_cpu("host,flags=+aes") == ("host", ",flags=+aes") and split_cpu("") == ("", "")
    assert valid_cpu_type("x86-64-v2-AES") and not valid_cpu_type("bad type") and not valid_cpu_type("a,b")
    assert valid_machine("pc-q35-8.1") and not valid_machine("q35;rm")
    assert vendor_of("AMD EPYC 7302") == "AMD" and vendor_of("Intel(R) Xeon") == "Intel" and vendor_of("ARM") is None


def test_level_flags_block_on_old_host_but_only_warn_when_not_current():
    rows = check_cpu_type("x86-64-v3", [INTEL_NEW, INTEL_OLD])
    assert lv(rows) == {"n1": "ok", "n2": "warn"}  # n2 lacks avx2/bmi/fma: the VM could not move there
    assert "avx2" in rows[1]["reason"] and "could not move" in rows[1]["reason"]
    old_current = dict(INTEL_OLD, is_current=True)
    assert lv(check_cpu_type("x86-64-v3", [old_current]))["n2"] == "block"  # on its own host it would not start


def test_v2_aes_needs_the_aes_flag():
    no_aes = dict(INTEL_OLD, is_current=True, flags=INTEL_OLD["flags"] - {"aes"})
    assert lv(check_cpu_type("x86-64-v2-AES", [no_aes]))["n2"] == "block"
    assert lv(check_cpu_type("x86-64-v2", [no_aes]))["n2"] == "ok"


def test_host_type_warns_when_cpu_models_differ_and_is_ok_when_same():
    assert lv(check_cpu_type("host", [INTEL_NEW, INTEL_OLD])) == {"n1": "warn", "n2": "warn"}
    twin = dict(INTEL_OLD, cpu_model=INTEL_NEW["cpu_model"])
    assert lv(check_cpu_type("host", [INTEL_NEW, twin])) == {"n1": "ok", "n2": "ok"}


def test_proxmox_vendor_strings():
    assert vendor_of("GenuineIntel") == "Intel" and vendor_of("AuthenticAMD") == "AMD"
    amd = dict(INTEL_NEW, name="n3", is_current=False, cpu_model="AMD Ryzen 7", models={"Skylake-Server": "GenuineIntel"})
    assert lv(check_cpu_type("Skylake-Server", [INTEL_NEW, amd]))["n3"] == "warn"


def test_named_model_vendor_and_availability():
    assert lv(check_cpu_type("Skylake-Server", [INTEL_NEW]))["n1"] == "ok"
    assert lv(check_cpu_type("Skylake-Server", [INTEL_NEW, INTEL_OLD]))["n2"] == "warn"  # n2's QEMU does not offer it
    amd = dict(INTEL_NEW, name="n3", is_current=False, cpu_model="AMD EPYC 7302", models={"Skylake-Server": "Intel"})
    rows = check_cpu_type("Skylake-Server", [INTEL_NEW, amd])
    assert lv(rows)["n3"] == "warn" and "Intel CPU model" in rows[1]["reason"]
    assert lv(check_cpu_type("Nope", [INTEL_NEW]))["n1"] == "block"
    assert lv(check_cpu_type("kvm64", [INTEL_NEW]))["n1"] == "ok"  # always available


def test_unread_host_is_unverified_not_ok():
    unknown = dict(INTEL_OLD, models=None, flags=None)
    assert lv(check_cpu_type("x86-64-v3", [unknown]))["n2"] == "warn"
    assert lv(check_machine("pc-q35-8.1", [dict(INTEL_NEW, machines=None)]))["n1"] == "warn"


def test_machine_types():
    a = dict(INTEL_NEW, machines={"pc-q35-8.1", "pc-q35-9.0"})
    b = dict(INTEL_OLD, machines={"pc-q35-8.1"})
    assert lv(check_machine("pc-q35-9.0", [a, b])) == {"n1": "ok", "n2": "warn"}
    assert lv(check_machine("pc-q35-9.0", [dict(b, is_current=True)]))["n2"] == "block"
    assert lv(check_machine("q35", [a, b])) == {"n1": "ok", "n2": "ok"}
