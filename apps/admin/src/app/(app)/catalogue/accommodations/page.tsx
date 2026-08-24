import { CatalogueResource } from "@/components/app/catalogue-resource";
import { ACCOMMODATIONS } from "@/lib/catalogue";

export default function Page() {
  return <CatalogueResource spec={ACCOMMODATIONS} />;
}
