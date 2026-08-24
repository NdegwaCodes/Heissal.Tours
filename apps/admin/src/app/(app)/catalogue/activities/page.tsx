import { CatalogueResource } from "@/components/app/catalogue-resource";
import { ACTIVITIES } from "@/lib/catalogue";

export default function Page() {
  return <CatalogueResource spec={ACTIVITIES} />;
}
