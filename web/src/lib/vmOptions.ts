// Plain-language names and short explanations for the VM settings a person picks. Proxmox's own values (l26, ovmf, virtio-scsi-single...) stay what is stored.
export type Choice = { value: string; label: string; help: string };

export const OS_TYPES: Choice[] = [
  { value: "l26", label: "Linux (kernel 2.6 or newer)", help: "Any current Linux: Ubuntu, Debian, Red Hat, Alma, Rocky, SUSE... Pick this for almost every Linux VM." },
  { value: "win11", label: "Windows 11 / Server 2022 / Server 2025", help: "Needs UEFI (OVMF) and, for Windows 11, a TPM 2.0 chip. PyXie ticks TPM for Windows 11 for you." },
  { value: "win10", label: "Windows 10 / Server 2016 / Server 2019", help: "Tunes Proxmox for these Windows versions (clock, timers, drivers)." },
  { value: "win8", label: "Windows 8.x / Server 2012", help: "Older Windows. Use only for legacy systems." },
  { value: "win7", label: "Windows 7 / Server 2008 R2", help: "Out of support. Use only for legacy systems." },
  { value: "w2k8", label: "Windows Vista / Server 2008", help: "Out of support. Use only for legacy systems." },
  { value: "wxp", label: "Windows XP / Server 2003", help: "Out of support. Use only for legacy systems." },
  { value: "w2k", label: "Windows 2000", help: "Out of support. Use only for legacy systems." },
  { value: "l24", label: "Linux 2.4 kernel", help: "Only for very old Linux." },
  { value: "solaris", label: "Solaris / OpenSolaris / illumos", help: "For Solaris-family systems." },
  { value: "other", label: "Other operating system", help: "BSD, appliances, anything not listed. Proxmox applies no OS-specific tuning." },
];

export const BIOS: Choice[] = [
  { value: "seabios", label: "SeaBIOS (legacy BIOS)", help: "The classic PC firmware. Works for nearly everything and is the Proxmox default." },
  { value: "ovmf", label: "OVMF (UEFI)", help: "Modern firmware. Needed for Windows 11, Secure Boot and booting from disks over 2 TB. Adds a small EFI disk." },
];

export const MACHINES: Choice[] = [
  { value: "", label: "Default (i440fx)", help: "Proxmox's classic PC model. Fine for most VMs." },
  { value: "q35", label: "q35 (modern PC)", help: "Newer chipset with PCIe. Needed for PCIe passthrough and recommended with UEFI." },
  { value: "pc", label: "i440fx (pc)", help: "The classic PC model, written out. Same as Default." },
];

export const SCSI_CONTROLLERS: Choice[] = [
  { value: "virtio-scsi-single", label: "VirtIO SCSI single (recommended)", help: "Fast, one controller per disk so each disk can use its own IO thread. Linux has it built in; Windows needs the VirtIO driver ISO." },
  { value: "virtio-scsi-pci", label: "VirtIO SCSI", help: "Fast, one controller shared by all disks." },
  { value: "lsi", label: "LSI 53C895A", help: "Old emulated controller that most installers see without extra drivers. Slower." },
  { value: "lsi53c810", label: "LSI 53C810", help: "Very old emulated controller. Legacy only." },
  { value: "megasas", label: "MegaRAID SAS 8708EM2", help: "Emulated hardware RAID card. Rarely needed." },
  { value: "pvscsi", label: "VMware PVSCSI", help: "VMware's paravirtual controller. Mainly useful for VMs imported from VMware." },
];

export const DISK_BUS: Choice[] = [
  { value: "scsi", label: "SCSI (recommended)", help: "Best all-round choice: fast, supports TRIM and hot-plug. Pair it with a VirtIO SCSI controller." },
  { value: "virtio", label: "VirtIO Block", help: "Also fast and simple. Fewer features than SCSI (no TRIM on older systems)." },
  { value: "sata", label: "SATA", help: "Looks like a normal disk to any OS, no extra drivers. Slower." },
  { value: "ide", label: "IDE", help: "Oldest and slowest. Only for very old systems." },
];

export const DISK_CACHE: Choice[] = [
  { value: "none", label: "No cache (recommended)", help: "Safe and fast on modern storage; data goes straight to the disk." },
  { value: "directsync", label: "Direct sync", help: "Safest: waits for the disk to confirm every write. Slowest." },
  { value: "writethrough", label: "Write through", help: "Reads are cached, writes wait for the disk. Safe, moderate speed." },
  { value: "writeback", label: "Write back", help: "Fast, but a host crash or power loss can lose recent writes." },
  { value: "unsafe", label: "Write back (unsafe)", help: "Fastest. Ignores flush requests: a crash can corrupt the disk. Only for throw-away VMs." },
];

export const NIC_MODELS: Choice[] = [
  { value: "virtio", label: "VirtIO (recommended)", help: "Fastest virtual network card. Linux has the driver; Windows needs the VirtIO driver ISO." },
  { value: "e1000", label: "Intel E1000", help: "Emulated Intel card that nearly every OS recognises. Slower than VirtIO." },
  { value: "e1000e", label: "Intel E1000E", help: "Newer emulated Intel card; the one Windows installers usually find." },
  { value: "vmxnet3", label: "VMware vmxnet3", help: "VMware's paravirtual card. Mainly for VMs imported from VMware." },
  { value: "rtl8139", label: "Realtek RTL8139", help: "Old emulated card. Legacy systems only." },
];

const CPU_HELP: Record<string, string> = {
  host: "Passes the real CPU through: fastest, all features. The VM can only live-migrate between hosts with the same CPU generation.",
  "x86-64-v2-AES": "Proxmox's default. A safe common baseline (2009-era instructions plus AES) that works on nearly every host, so VMs migrate freely.",
  "x86-64-v2": "Common baseline without AES acceleration. Rarely needed.",
  "x86-64-v3": "Adds AVX2 and newer instructions (about 2015+ CPUs). Faster for modern software; every host must support it to migrate.",
  "x86-64-v4": "Adds AVX-512 (only some server CPUs). Every host must support it to migrate.",
  kvm64: "Very old generic CPU. Maximum compatibility, minimum features.",
  qemu64: "QEMU's generic CPU. Compatibility fallback.",
  max: "Everything QEMU can emulate. For testing; not for migrating between different hosts.",
};
export function cpuHelp(name: string): string {
  if (CPU_HELP[name]) return CPU_HELP[name];
  if (/^(Skylake|Cascadelake|Icelake|Haswell|Broadwell|Nehalem|Westmere|SandyBridge|IvyBridge|Sapphire|Cooper|Snowridge|Penryn|Conroe|Opteron|EPYC|Dhyana|Denverton|KnightsMill|Cascade)/i.test(name)) return "A named CPU generation. Pins the VM to the features of that generation, so it can move between hosts that are at least that new.";
  return "A CPU model offered by your Proxmox hosts.";
}

export const GIB_MB = 1024;
export const mbToGb = (mb: unknown): string => { const n = Number(mb); return Number.isFinite(n) && n > 0 ? String(Math.round((n / GIB_MB) * 100) / 100) : ""; };
export const gbToMb = (gb: string): number => Math.round(parseFloat(gb) * GIB_MB);
