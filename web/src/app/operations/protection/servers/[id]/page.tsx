import { redirect } from "next/navigation";

// The backup server page now lives under Hosts & Clusters; old links keep working.
export default function PbsServerRedirect({ params }: { params: { id: string } }) {
  redirect(`/infrastructure/backup-servers/${params.id}`);
}
