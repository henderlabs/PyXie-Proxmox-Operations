import { redirect } from "next/navigation";

export default function OldDefaultsPage() {
  redirect("/infrastructure/workloads/defaults");
}
